// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {Test} from "forge-std/Test.sol";
import {CorporateSink} from "../src/CorporateSink.sol";
import {PublicSink} from "../src/PublicSink.sol";
import {StreamingEngine} from "../src/StreamingEngine.sol";
import {WitnessDAO} from "../src/WitnessDAO.sol";

contract LocalWitnessLoopTest is Test {
    address internal corporateAdmin = makeAddr("corporate");
    address internal relayer = makeAddr("relayer");

    StreamingEngine internal engine;
    PublicSink internal publicSink;
    CorporateSink internal corporateSink;
    WitnessDAO internal dao;

    uint256 internal constant RATE_PER_USER = 1_000_000_000_000;

    function setUp() public {
        vm.warp(1_000_000);
        engine = new StreamingEngine(RATE_PER_USER, 10);
        publicSink = new PublicSink(address(engine));
        corporateSink = new CorporateSink(corporateAdmin, 14 days, 2 hours);
        dao = new WitnessDAO(
            address(engine),
            address(corporateSink),
            corporateAdmin,
            5,
            9000
        );
        engine.setWitnessDAO(address(dao));
        corporateSink.setWitnessDAO(address(dao));
    }

    function registerWitness(uint256 key) internal returns (address witness) {
        witness = vm.addr(key);
        uint256 deadline = block.timestamp + 1 hours;
        bytes32 digest = dao.hashRegister(witness, 0, deadline);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        WitnessDAO.RegisterSignature[] memory signatures =
            new WitnessDAO.RegisterSignature[](1);
        signatures[0] = WitnessDAO.RegisterSignature({
            witness: witness,
            nonce: 0,
            deadline: deadline,
            v: v,
            r: r,
            s: s
        });
        vm.prank(relayer);
        dao.batchRegister(signatures);
    }

    function scheduleAndPropose(uint256 newUserCount)
        internal
        returns (uint256 proposalId)
    {
        uint256 scheduledAt = block.timestamp + 14 days;
        vm.prank(corporateAdmin);
        corporateSink.scheduleBroadcast(scheduledAt, keccak256("live"));
        vm.warp(scheduledAt);
        vm.prank(corporateAdmin);
        proposalId = dao.proposeUserCount(newUserCount, keccak256("evidence"));
    }

    function vote(
        uint256 key,
        uint256 proposalId,
        bool support,
        bytes32 payloadHash
    ) internal returns (WitnessDAO.VoteSignature memory signature) {
        address witness = vm.addr(key);
        uint256 nonce = dao.voteNonces(witness);
        uint256 deadline = block.timestamp + 1 hours;
        bytes32 reasonHash = support ? bytes32(0) : keccak256("reason");
        bytes32 digest = dao.hashVote(
            witness,
            proposalId,
            support,
            reasonHash,
            payloadHash,
            nonce,
            deadline
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        signature = WitnessDAO.VoteSignature({
            witness: witness,
            proposalId: proposalId,
            support: support,
            reasonHash: reasonHash,
            payloadHash: payloadHash,
            nonce: nonce,
            deadline: deadline,
            v: v,
            r: r,
            s: s
        });
    }

    function testStreamingEngineCheckpointsBeforeRateChange() public {
        uint256 beforeBalance = publicSink.getTotalUTO();
        vm.warp(block.timestamp + 1 days);
        uint256 accrued = publicSink.getTotalUTO();
        assertGt(accrued, beforeBalance);

        vm.prank(address(dao));
        engine.updateUserCount(20);

        assertEq(engine.baseBalance(), accrued);
        assertEq(engine.rate(), 20 * RATE_PER_USER);
        assertEq(publicSink.getTotalUTO(), accrued);
    }

    function testRegisterRejectsReplayAndExpiredSignature() public {
        address witness = registerWitness(11);
        assertTrue(dao.registeredWitnesses(witness));

        uint256 deadline = block.timestamp - 1;
        bytes32 digest = dao.hashRegister(witness, 1, deadline);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(11, digest);
        WitnessDAO.RegisterSignature[] memory signatures =
            new WitnessDAO.RegisterSignature[](1);
        signatures[0] = WitnessDAO.RegisterSignature(
            witness, 1, deadline, v, r, s
        );

        vm.expectRevert(WitnessDAO.SignatureExpired.selector);
        dao.batchRegister(signatures);
    }

    function testRegisterRejectsWrongSigner() public {
        address witness = vm.addr(11);
        uint256 deadline = block.timestamp + 1 hours;
        bytes32 digest = dao.hashRegister(witness, 0, deadline);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(12, digest);
        WitnessDAO.RegisterSignature[] memory signatures =
            new WitnessDAO.RegisterSignature[](1);
        signatures[0] = WitnessDAO.RegisterSignature(
            witness, 0, deadline, v, r, s
        );

        vm.expectRevert(WitnessDAO.InvalidSignature.selector);
        dao.batchRegister(signatures);
    }

    function testProposalRequiresBroadcastWindow() public {
        vm.prank(corporateAdmin);
        vm.expectRevert(WitnessDAO.OutsideBroadcastWindow.selector);
        dao.proposeUserCount(20, keccak256("evidence"));
    }

    function testVoteRejectsWrongPayloadHash() public {
        for (uint256 key = 1; key <= 5; key++) {
            registerWitness(key);
        }
        uint256 proposalId = scheduleAndPropose(20);
        bytes32 correctPayload = dao.proposalPayloadHash(proposalId);
        WitnessDAO.VoteSignature[] memory signatures =
            new WitnessDAO.VoteSignature[](1);
        signatures[0] = vote(
            1,
            proposalId,
            true,
            bytes32(uint256(correctPayload) + 1)
        );

        vm.expectRevert(WitnessDAO.PayloadHashMismatch.selector);
        dao.batchVote(signatures);
    }

    function testVoteRejectsWrongProposalId() public {
        for (uint256 key = 1; key <= 5; key++) {
            registerWitness(key);
        }
        uint256 proposalId = scheduleAndPropose(20);
        bytes32 payloadHash = dao.proposalPayloadHash(proposalId);
        WitnessDAO.VoteSignature[] memory signatures =
            new WitnessDAO.VoteSignature[](1);
        signatures[0] = vote(1, proposalId + 1, true, payloadHash);

        vm.expectRevert(WitnessDAO.ProposalIdMismatch.selector);
        dao.batchVote(signatures);
    }

    function testLowApprovalDoesNotExecute() public {
        for (uint256 key = 1; key <= 5; key++) {
            registerWitness(key);
        }
        uint256 proposalId = scheduleAndPropose(20);
        bytes32 payloadHash = dao.proposalPayloadHash(proposalId);
        WitnessDAO.VoteSignature[] memory signatures =
            new WitnessDAO.VoteSignature[](5);
        for (uint256 key = 1; key <= 5; key++) {
            signatures[key - 1] = vote(
                key,
                proposalId,
                key != 5,
                payloadHash
            );
        }

        dao.batchVote(signatures);

        assertTrue(dao.proposalActive(proposalId));
        assertFalse(dao.proposalExecuted(proposalId));
        assertEq(engine.rate(), 10 * RATE_PER_USER);
    }

    function testDuplicateVoteIsRejected() public {
        for (uint256 key = 1; key <= 5; key++) {
            registerWitness(key);
        }
        uint256 proposalId = scheduleAndPropose(20);
        WitnessDAO.VoteSignature[] memory signatures =
            new WitnessDAO.VoteSignature[](1);
        signatures[0] = vote(
            1,
            proposalId,
            true,
            dao.proposalPayloadHash(proposalId)
        );
        dao.batchVote(signatures);

        vm.expectRevert(WitnessDAO.DuplicateVote.selector);
        dao.batchVote(signatures);
    }

    function testProposalAfterBroadcastWindowIsRejected() public {
        uint256 scheduledAt = block.timestamp + 14 days;
        vm.prank(corporateAdmin);
        corporateSink.scheduleBroadcast(scheduledAt, keccak256("live"));
        vm.warp(scheduledAt + 2 hours + 1);

        vm.prank(corporateAdmin);
        vm.expectRevert(WitnessDAO.OutsideBroadcastWindow.selector);
        dao.proposeUserCount(20, keccak256("evidence"));
    }

    function testBroadcastSchedulesRespectMinimumInterval() public {
        uint256 first = block.timestamp + 14 days;
        vm.startPrank(corporateAdmin);
        corporateSink.scheduleBroadcast(first, keccak256("first"));
        vm.expectRevert(CorporateSink.BroadcastTooSoon.selector);
        corporateSink.scheduleBroadcast(first + 13 days, keccak256("second"));
        vm.stopPrank();
    }

    function testCompensationProposalRecordsCorporateLedger() public {
        for (uint256 key = 1; key <= 5; key++) {
            registerWitness(key);
        }
        uint256 scheduledAt = block.timestamp + 14 days;
        vm.prank(corporateAdmin);
        corporateSink.scheduleBroadcast(scheduledAt, keccak256("live"));
        vm.warp(scheduledAt);
        vm.prank(corporateAdmin);
        uint256 proposalId = dao.proposeCompensation(
            100,
            keccak256("request"),
            keccak256("evidence")
        );
        bytes32 payloadHash = dao.proposalPayloadHash(proposalId);
        WitnessDAO.VoteSignature[] memory signatures =
            new WitnessDAO.VoteSignature[](5);
        for (uint256 key = 1; key <= 5; key++) {
            signatures[key - 1] = vote(
                key,
                proposalId,
                true,
                payloadHash
            );
        }

        dao.batchVote(signatures);

        assertEq(corporateSink.getCompensation(), 100);
    }

    function testPublicSinkHasNoOwnerMutationSurface() public {
        assertTrue(dao.getDomainSeparator() != bytes32(0));
        (bool ownerOk,) =
            address(publicSink).call(abi.encodeWithSignature("owner()"));
        (bool mutationOk,) = address(publicSink).call(
            abi.encodeWithSignature("setStreamingEngine(address)", address(1))
        );
        assertFalse(ownerOk);
        assertFalse(mutationOk);
    }

    function testFiveWitnessLocalLoopExecutesUserCountProposal() public {
        for (uint256 key = 1; key <= 5; key++) {
            registerWitness(key);
        }
        uint256 proposalId = scheduleAndPropose(20);
        bytes32 payloadHash = dao.proposalPayloadHash(proposalId);
        WitnessDAO.VoteSignature[] memory signatures =
            new WitnessDAO.VoteSignature[](5);
        for (uint256 key = 1; key <= 5; key++) {
            signatures[key - 1] = vote(
                key,
                proposalId,
                true,
                payloadHash
            );
        }

        vm.prank(relayer);
        dao.batchVote(signatures);

        assertFalse(dao.proposalActive(proposalId));
        assertTrue(dao.proposalExecuted(proposalId));
        assertEq(engine.rate(), 20 * RATE_PER_USER);
    }

    function testBatchScaleWithSixtyNineWitnesses() public {
        StreamingEngine scaleEngine = new StreamingEngine(RATE_PER_USER, 10);
        CorporateSink scaleCorporate =
            new CorporateSink(corporateAdmin, 14 days, 2 hours);
        WitnessDAO scaleDao = new WitnessDAO(
            address(scaleEngine),
            address(scaleCorporate),
            corporateAdmin,
            69,
            9000
        );
        scaleEngine.setWitnessDAO(address(scaleDao));
        scaleCorporate.setWitnessDAO(address(scaleDao));

        WitnessDAO.RegisterSignature[] memory registrations =
            new WitnessDAO.RegisterSignature[](69);
        for (uint256 key = 100; key < 169; key++) {
            address witness = vm.addr(key);
            uint256 deadline = block.timestamp + 1 hours;
            bytes32 digest = scaleDao.hashRegister(witness, 0, deadline);
            (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
            registrations[key - 100] = WitnessDAO.RegisterSignature(
                witness, 0, deadline, v, r, s
            );
        }
        scaleDao.batchRegister(registrations);

        uint256 scheduledAt = block.timestamp + 14 days;
        vm.prank(corporateAdmin);
        scaleCorporate.scheduleBroadcast(scheduledAt, keccak256("live"));
        vm.warp(scheduledAt);
        vm.prank(corporateAdmin);
        uint256 proposalId =
            scaleDao.proposeUserCount(30, keccak256("evidence"));
        bytes32 payloadHash = scaleDao.proposalPayloadHash(proposalId);

        WitnessDAO.VoteSignature[] memory votes =
            new WitnessDAO.VoteSignature[](69);
        for (uint256 key = 100; key < 169; key++) {
            address witness = vm.addr(key);
            uint256 deadline = block.timestamp + 1 hours;
            bytes32 digest = scaleDao.hashVote(
                witness,
                proposalId,
                true,
                bytes32(0),
                payloadHash,
                0,
                deadline
            );
            (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
            votes[key - 100] = WitnessDAO.VoteSignature(
                witness,
                proposalId,
                true,
                bytes32(0),
                payloadHash,
                0,
                deadline,
                v,
                r,
                s
            );
        }
        scaleDao.batchVote(votes);

        assertTrue(scaleDao.proposalExecuted(proposalId));
        assertEq(scaleEngine.rate(), 30 * RATE_PER_USER);
    }
}
