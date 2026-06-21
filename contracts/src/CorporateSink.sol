// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

contract CorporateSink {
    error NotBootstrapper();
    error WitnessDAOAlreadySet();
    error NotWitnessDAO();
    error NotCorporateAdmin();
    error BroadcastTooSoon();
    error BroadcastNotReached();
    error CertificateAlreadySet();

    address public immutable bootstrapper;
    address public immutable corporateAdmin;
    uint256 public immutable minBroadcastInterval;
    uint256 public immutable broadcastWindow;
    address public witnessDAO;

    uint256 public nextBroadcastTime;
    uint256 public compensation;
    mapping(uint256 => bytes32) private certificates;

    event WitnessDAOSet(address indexed witnessDAO);
    event BroadcastScheduled(
        uint256 indexed timestamp,
        bytes32 liveMetadataHash
    );
    event CertificateUploaded(uint256 indexed index, bytes32 hash);
    event CompensationRecorded(
        uint256 amount,
        bytes32 requestHash,
        bytes32 evidenceBundleHash
    );

    constructor(
        address corporateAdmin_,
        uint256 minBroadcastInterval_,
        uint256 broadcastWindow_
    ) {
        bootstrapper = msg.sender;
        corporateAdmin = corporateAdmin_;
        minBroadcastInterval = minBroadcastInterval_;
        broadcastWindow = broadcastWindow_;
    }

    function setWitnessDAO(address witnessDAO_) external {
        if (msg.sender != bootstrapper) revert NotBootstrapper();
        if (witnessDAO != address(0)) revert WitnessDAOAlreadySet();
        if (witnessDAO_ == address(0)) revert NotWitnessDAO();
        witnessDAO = witnessDAO_;
        emit WitnessDAOSet(witnessDAO_);
    }

    function scheduleBroadcast(
        uint256 timestamp,
        bytes32 liveMetadataHash
    ) external {
        if (msg.sender != corporateAdmin) revert NotCorporateAdmin();
        if (
            timestamp < block.timestamp ||
            (
                nextBroadcastTime != 0 &&
                timestamp < nextBroadcastTime + minBroadcastInterval
            )
        ) revert BroadcastTooSoon();
        nextBroadcastTime = timestamp;
        emit BroadcastScheduled(timestamp, liveMetadataHash);
    }

    function uploadCertificate(uint256 index, bytes32 hash) external {
        if (msg.sender != corporateAdmin) revert NotCorporateAdmin();
        if (block.timestamp < nextBroadcastTime) revert BroadcastNotReached();
        if (certificates[index] != bytes32(0)) {
            revert CertificateAlreadySet();
        }
        certificates[index] = hash;
        emit CertificateUploaded(index, hash);
    }

    function getCorporateCSR(uint256 index) external view returns (bytes32) {
        return certificates[index];
    }

    function getCompensation() external view returns (uint256) {
        return compensation;
    }

    function isProposalWindowOpen(uint256 timestamp)
        external
        view
        returns (bool)
    {
        return
            nextBroadcastTime != 0 &&
            timestamp >= nextBroadcastTime &&
            timestamp <= nextBroadcastTime + broadcastWindow;
    }

    function recordCompensation(
        uint256 amount,
        bytes32 requestHash,
        bytes32 evidenceBundleHash
    ) external {
        if (msg.sender != witnessDAO) revert NotWitnessDAO();
        compensation += amount;
        emit CompensationRecorded(amount, requestHash, evidenceBundleHash);
    }
}
