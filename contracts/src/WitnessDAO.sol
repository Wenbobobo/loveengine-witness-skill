// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ICorporateSink} from "./interfaces/ICorporateSink.sol";
import {IStreamingEngine} from "./interfaces/IStreamingEngine.sol";

contract WitnessDAO is EIP712 {
    error InvalidSignature();
    error InvalidNonce();
    error SignatureExpired();
    error WitnessAlreadyRegistered();
    error WitnessNotRegistered();
    error ProposalNotActive();
    error ProposalIdMismatch();
    error PayloadHashMismatch();
    error DuplicateVote();
    error NotCorporateAdmin();
    error OutsideBroadcastWindow();
    error ProposalExpired();
    error ProposalNotExpired();
    error RefundWithdrawalFailed();

    enum ProposalType {
        USER_COUNT,
        COMPENSATION
    }

    struct RegisterSignature {
        address witness;
        uint256 nonce;
        uint256 deadline;
        uint8 v;
        bytes32 r;
        bytes32 s;
    }

    struct VoteSignature {
        address witness;
        uint256 proposalId;
        bool support;
        bytes32 reasonHash;
        bytes32 payloadHash;
        uint256 nonce;
        uint256 deadline;
        uint8 v;
        bytes32 r;
        bytes32 s;
    }

    struct Proposal {
        ProposalType proposalType;
        uint256 newUserCount;
        uint256 amount;
        bytes32 requestHash;
        bytes32 evidenceBundleHash;
        bytes32 payloadHash;
        uint256 totalVotes;
        uint256 supportVotes;
        uint256 createdAt;
        uint256 votingDeadline;
        bool active;
        bool executed;
    }

    uint256 public constant VOTING_PERIOD = 1 days;
    uint256 public constant REFUND_BASE_OVERHEAD = 25_000;

    bytes32 public constant REGISTER_TYPEHASH =
        keccak256(
            "Register(address witness,uint256 nonce,uint256 deadline)"
        );
    bytes32 public constant VOTE_TYPEHASH =
        keccak256(
            "Vote(address witness,uint256 proposalId,bool support,bytes32 reasonHash,bytes32 payloadHash,uint256 nonce,uint256 deadline)"
        );

    IStreamingEngine public immutable streamingEngine;
    ICorporateSink public immutable corporateSink;
    address public immutable corporateAdmin;
    uint256 public immutable minValidVotes;
    uint256 public immutable approvalThresholdBps;

    uint256 public activeProposalId;
    uint256 private nextProposalId = 1;

    mapping(address => bool) public registeredWitnesses;
    mapping(address => uint256) public registerNonces;
    mapping(address => uint256) public voteNonces;
    mapping(uint256 => Proposal) private proposals;
    mapping(uint256 => mapping(address => bool)) public hasVoted;
    mapping(address => bool) public relayerRefundAllowed;
    mapping(address => uint256) public relayerRefundMaxWeiPerCall;
    mapping(address => uint256) public relayerRefundCredits;

    event WitnessRegistered(address indexed witness);
    event ProposalCreated(
        uint256 indexed proposalId,
        ProposalType proposalType,
        bytes32 evidenceBundleHash,
        bytes32 payloadHash
    );
    event VoteAccepted(
        uint256 indexed proposalId,
        address indexed witness,
        bool support,
        bytes32 reasonHash
    );
    event ProposalFinalized(
        uint256 indexed proposalId,
        bool passed,
        uint256 totalVotes,
        uint256 supportVotes
    );
    event ProposalExecuted(uint256 indexed proposalId);
    event ProposalFailed(
        uint256 indexed proposalId,
        uint256 totalVotes,
        uint256 supportVotes
    );
    event RelayerRefundPolicySet(
        address indexed relayer,
        bool allowed,
        uint256 maxWeiPerCall
    );
    event RelayerRefundCredited(address indexed relayer, uint256 amount);
    event RelayerRefundWithdrawn(address indexed relayer, uint256 amount);

    constructor(
        address streamingEngine_,
        address corporateSink_,
        address corporateAdmin_,
        uint256 minValidVotes_,
        uint256 approvalThresholdBps_
    ) EIP712("LoveEngine WitnessDAO", "1") {
        streamingEngine = IStreamingEngine(streamingEngine_);
        corporateSink = ICorporateSink(corporateSink_);
        corporateAdmin = corporateAdmin_;
        minValidVotes = minValidVotes_;
        approvalThresholdBps = approvalThresholdBps_;
    }

    function getDomainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }

    function hashRegister(
        address witness,
        uint256 nonce,
        uint256 deadline
    ) public view returns (bytes32) {
        return
            _hashTypedDataV4(
                keccak256(
                    abi.encode(
                        REGISTER_TYPEHASH,
                        witness,
                        nonce,
                        deadline
                    )
                )
            );
    }

    function hashVote(
        address witness,
        uint256 proposalId,
        bool support,
        bytes32 reasonHash,
        bytes32 payloadHash,
        uint256 nonce,
        uint256 deadline
    ) public view returns (bytes32) {
        return
            _hashTypedDataV4(
                keccak256(
                    abi.encode(
                        VOTE_TYPEHASH,
                        witness,
                        proposalId,
                        support,
                        reasonHash,
                        payloadHash,
                        nonce,
                        deadline
                    )
                )
            );
    }

    function setRelayerRefundPolicy(
        address relayer,
        bool allowed,
        uint256 maxWeiPerCall
    ) external {
        if (msg.sender != corporateAdmin) revert NotCorporateAdmin();
        relayerRefundAllowed[relayer] = allowed;
        relayerRefundMaxWeiPerCall[relayer] = maxWeiPerCall;
        emit RelayerRefundPolicySet(relayer, allowed, maxWeiPerCall);
    }

    function withdrawRelayerRefund() external {
        uint256 amount = relayerRefundCredits[msg.sender];
        relayerRefundCredits[msg.sender] = 0;
        (bool success,) = payable(msg.sender).call{value: amount}("");
        if (!success) revert RefundWithdrawalFailed();
        emit RelayerRefundWithdrawn(msg.sender, amount);
    }

    receive() external payable {}

    function batchRegister(RegisterSignature[] calldata signatures) external {
        uint256 startGas = gasleft();
        for (uint256 index = 0; index < signatures.length; index++) {
            RegisterSignature calldata item = signatures[index];
            if (block.timestamp > item.deadline) revert SignatureExpired();
            if (registeredWitnesses[item.witness]) {
                revert WitnessAlreadyRegistered();
            }
            if (item.nonce != registerNonces[item.witness]) {
                revert InvalidNonce();
            }
            address signer = ECDSA.recover(
                hashRegister(item.witness, item.nonce, item.deadline),
                item.v,
                item.r,
                item.s
            );
            if (signer != item.witness) revert InvalidSignature();
            registerNonces[item.witness]++;
            registeredWitnesses[item.witness] = true;
            emit WitnessRegistered(item.witness);
        }
        _creditRelayerRefund(startGas);
    }

    function proposeUserCount(
        uint256 newUserCount,
        bytes32 evidenceBundleHash
    ) external returns (uint256 proposalId) {
        _requireCorporateAndWindow();
        bytes32 payloadHash = keccak256(
            abi.encode(
                ProposalType.USER_COUNT,
                newUserCount,
                evidenceBundleHash
            )
        );
        proposalId = _createProposal(
            ProposalType.USER_COUNT,
            newUserCount,
            0,
            bytes32(0),
            evidenceBundleHash,
            payloadHash
        );
    }

    function proposeCompensation(
        uint256 amount,
        bytes32 requestHash,
        bytes32 evidenceBundleHash
    ) external returns (uint256 proposalId) {
        _requireCorporateAndWindow();
        bytes32 payloadHash = keccak256(
            abi.encode(
                ProposalType.COMPENSATION,
                amount,
                requestHash,
                evidenceBundleHash
            )
        );
        proposalId = _createProposal(
            ProposalType.COMPENSATION,
            0,
            amount,
            requestHash,
            evidenceBundleHash,
            payloadHash
        );
    }

    function batchVote(VoteSignature[] calldata signatures) external {
        uint256 startGas = gasleft();
        Proposal storage active = proposals[activeProposalId];
        if (active.active && block.timestamp > active.votingDeadline) {
            revert ProposalExpired();
        }
        for (uint256 index = 0; index < signatures.length; index++) {
            VoteSignature calldata item = signatures[index];
            if (item.proposalId != activeProposalId) {
                revert ProposalIdMismatch();
            }
            Proposal storage proposal = proposals[item.proposalId];
            if (!proposal.active) revert ProposalNotActive();
            if (item.payloadHash != proposal.payloadHash) {
                revert PayloadHashMismatch();
            }
            if (!registeredWitnesses[item.witness]) {
                revert WitnessNotRegistered();
            }
            if (hasVoted[item.proposalId][item.witness]) {
                revert DuplicateVote();
            }
            if (block.timestamp > item.deadline) revert SignatureExpired();
            if (item.nonce != voteNonces[item.witness]) {
                revert InvalidNonce();
            }
            address signer = ECDSA.recover(
                hashVote(
                    item.witness,
                    item.proposalId,
                    item.support,
                    item.reasonHash,
                    item.payloadHash,
                    item.nonce,
                    item.deadline
                ),
                item.v,
                item.r,
                item.s
            );
            if (signer != item.witness) revert InvalidSignature();

            voteNonces[item.witness]++;
            hasVoted[item.proposalId][item.witness] = true;
            proposal.totalVotes++;
            if (item.support) proposal.supportVotes++;
            emit VoteAccepted(
                item.proposalId,
                item.witness,
                item.support,
                item.reasonHash
            );
        }

        if (
            active.active &&
            active.totalVotes >= minValidVotes &&
            active.supportVotes * 10_000 >=
            active.totalVotes * approvalThresholdBps
        ) {
            _execute(activeProposalId, active);
        }
        _creditRelayerRefund(startGas);
    }

    function proposalPayloadHash(uint256 proposalId)
        external
        view
        returns (bytes32)
    {
        return proposals[proposalId].payloadHash;
    }

    function proposalActive(uint256 proposalId) external view returns (bool) {
        return proposals[proposalId].active;
    }

    function proposalExecuted(uint256 proposalId)
        external
        view
        returns (bool)
    {
        return proposals[proposalId].executed;
    }

    function proposalCreatedAt(uint256 proposalId)
        external
        view
        returns (uint256)
    {
        return proposals[proposalId].createdAt;
    }

    function proposalVotingDeadline(uint256 proposalId)
        external
        view
        returns (uint256)
    {
        return proposals[proposalId].votingDeadline;
    }

    function proposalVoteCounts(uint256 proposalId)
        external
        view
        returns (uint256 totalVotes, uint256 supportVotes)
    {
        Proposal storage proposal = proposals[proposalId];
        return (proposal.totalVotes, proposal.supportVotes);
    }

    function finalizeProposal(uint256 proposalId) external {
        Proposal storage proposal = proposals[proposalId];
        if (!proposal.active) revert ProposalNotActive();
        if (block.timestamp <= proposal.votingDeadline) {
            revert ProposalNotExpired();
        }
        if (
            proposal.totalVotes >= minValidVotes &&
            proposal.supportVotes * 10_000 >=
            proposal.totalVotes * approvalThresholdBps
        ) {
            _execute(proposalId, proposal);
            return;
        }
        proposal.active = false;
        activeProposalId = 0;
        emit ProposalFinalized(
            proposalId,
            false,
            proposal.totalVotes,
            proposal.supportVotes
        );
        emit ProposalFailed(
            proposalId,
            proposal.totalVotes,
            proposal.supportVotes
        );
    }

    function _requireCorporateAndWindow() internal view {
        if (msg.sender != corporateAdmin) revert NotCorporateAdmin();
        if (!corporateSink.isProposalWindowOpen(block.timestamp)) {
            revert OutsideBroadcastWindow();
        }
        if (
            activeProposalId != 0 &&
            proposals[activeProposalId].active
        ) revert ProposalNotActive();
    }

    function _createProposal(
        ProposalType proposalType,
        uint256 newUserCount,
        uint256 amount,
        bytes32 requestHash,
        bytes32 evidenceBundleHash,
        bytes32 payloadHash
    ) internal returns (uint256 proposalId) {
        proposalId = nextProposalId++;
        proposals[proposalId] = Proposal({
            proposalType: proposalType,
            newUserCount: newUserCount,
            amount: amount,
            requestHash: requestHash,
            evidenceBundleHash: evidenceBundleHash,
            payloadHash: payloadHash,
            totalVotes: 0,
            supportVotes: 0,
            createdAt: block.timestamp,
            votingDeadline: block.timestamp + VOTING_PERIOD,
            active: true,
            executed: false
        });
        activeProposalId = proposalId;
        emit ProposalCreated(
            proposalId,
            proposalType,
            evidenceBundleHash,
            payloadHash
        );
    }

    function _execute(uint256 proposalId, Proposal storage proposal) internal {
        proposal.active = false;
        proposal.executed = true;
        activeProposalId = 0;
        emit ProposalFinalized(
            proposalId,
            true,
            proposal.totalVotes,
            proposal.supportVotes
        );
        if (proposal.proposalType == ProposalType.USER_COUNT) {
            streamingEngine.updateUserCount(proposal.newUserCount);
        } else {
            corporateSink.recordCompensation(
                proposal.amount,
                proposal.requestHash,
                proposal.evidenceBundleHash
            );
        }
        emit ProposalExecuted(proposalId);
    }

    function _creditRelayerRefund(uint256 startGas) internal {
        if (!relayerRefundAllowed[msg.sender]) return;
        uint256 maxRefund = relayerRefundMaxWeiPerCall[msg.sender];
        if (maxRefund == 0) return;
        uint256 refund = (startGas - gasleft() + REFUND_BASE_OVERHEAD) *
            tx.gasprice;
        if (refund > maxRefund) refund = maxRefund;
        if (refund == 0) return;
        relayerRefundCredits[msg.sender] += refund;
        emit RelayerRefundCredited(msg.sender, refund);
    }
}
