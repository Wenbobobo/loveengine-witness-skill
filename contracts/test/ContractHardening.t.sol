// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {Test} from "forge-std/Test.sol";
import {CorporateSink} from "../src/CorporateSink.sol";
import {WitnessDAO} from "../src/WitnessDAO.sol";

contract ContractHardeningTest is Test {
    address internal corporateAdmin = makeAddr("corporate");
    address internal relayer = makeAddr("relayer");
    address internal streamingEngine = makeAddr("streaming-engine");
    address internal corporateSinkAddress = makeAddr("corporate-sink");

    function newDAO() internal returns (WitnessDAO) {
        return new WitnessDAO(
            streamingEngine,
            corporateSinkAddress,
            corporateAdmin,
            5,
            9000
        );
    }

    function registerWitness(
        WitnessDAO dao,
        uint256 key
    ) internal returns (address witness) {
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

    function testWitnessDAOConstructorRejectsZeroAddresses() public {
        vm.expectRevert(WitnessDAO.ZeroAddress.selector);
        new WitnessDAO(
            address(0),
            corporateSinkAddress,
            corporateAdmin,
            5,
            9000
        );

        vm.expectRevert(WitnessDAO.ZeroAddress.selector);
        new WitnessDAO(
            streamingEngine,
            address(0),
            corporateAdmin,
            5,
            9000
        );

        vm.expectRevert(WitnessDAO.ZeroAddress.selector);
        new WitnessDAO(
            streamingEngine,
            corporateSinkAddress,
            address(0),
            5,
            9000
        );
    }

    function testWitnessDAOConstructorRejectsInvalidGovernanceValues() public {
        vm.expectRevert(WitnessDAO.InvalidMinimumVotes.selector);
        new WitnessDAO(
            streamingEngine,
            corporateSinkAddress,
            corporateAdmin,
            0,
            9000
        );

        vm.expectRevert(WitnessDAO.InvalidApprovalThreshold.selector);
        new WitnessDAO(
            streamingEngine,
            corporateSinkAddress,
            corporateAdmin,
            5,
            0
        );

        vm.expectRevert(WitnessDAO.InvalidApprovalThreshold.selector);
        new WitnessDAO(
            streamingEngine,
            corporateSinkAddress,
            corporateAdmin,
            5,
            10_001
        );
    }

    function testWitnessDAOConstructorAcceptsThresholdBoundaries() public {
        WitnessDAO minimumThresholdDAO = new WitnessDAO(
            streamingEngine,
            corporateSinkAddress,
            corporateAdmin,
            1,
            1
        );
        WitnessDAO maximumThresholdDAO = new WitnessDAO(
            streamingEngine,
            corporateSinkAddress,
            corporateAdmin,
            1,
            10_000
        );

        assertEq(minimumThresholdDAO.approvalThresholdBps(), 1);
        assertEq(maximumThresholdDAO.approvalThresholdBps(), 10_000);
    }

    function testCorporateSinkConstructorRejectsZeroAdmin() public {
        vm.expectRevert(CorporateSink.ZeroAddress.selector);
        new CorporateSink(address(0), 14 days, 2 hours);
    }

    function testBatchOperationsRejectEmptyArrays() public {
        WitnessDAO dao = newDAO();
        WitnessDAO.RegisterSignature[] memory registrations =
            new WitnessDAO.RegisterSignature[](0);
        vm.expectRevert(WitnessDAO.EmptyBatch.selector);
        dao.batchRegister(registrations);

        WitnessDAO.VoteSignature[] memory votes =
            new WitnessDAO.VoteSignature[](0);
        vm.expectRevert(WitnessDAO.EmptyBatch.selector);
        dao.batchVote(votes);
    }

    function testRefundCreditsCannotExceedAvailableBalance() public {
        WitnessDAO dao = newDAO();
        vm.deal(address(dao), 1 wei);
        vm.txGasPrice(1 gwei);
        vm.prank(corporateAdmin);
        dao.setRelayerRefundPolicy(relayer, true, 0.01 ether);

        registerWitness(dao, 44);

        assertEq(dao.relayerRefundCredits(relayer), 1 wei);
        assertEq(dao.totalRelayerRefundCredits(), 1 wei);
        assertEq(address(dao).balance, 1 wei);

        address secondWitness = registerWitness(dao, 45);

        assertTrue(dao.registeredWitnesses(secondWitness));
        assertEq(dao.relayerRefundCredits(relayer), 1 wei);
        assertEq(dao.totalRelayerRefundCredits(), 1 wei);

        uint256 balanceBefore = relayer.balance;
        vm.prank(relayer);
        dao.withdrawRelayerRefund();

        assertEq(relayer.balance, balanceBefore + 1 wei);
        assertEq(dao.relayerRefundCredits(relayer), 0);
        assertEq(dao.totalRelayerRefundCredits(), 0);
        assertEq(address(dao).balance, 0);
    }

    function testLaterBroadcastDoesNotBlockEarlierCertificate() public {
        CorporateSink corporateSink =
            new CorporateSink(corporateAdmin, 14 days, 2 hours);
        uint256 firstBroadcast = block.timestamp + 14 days;
        uint256 secondBroadcast = firstBroadcast + 14 days;
        vm.startPrank(corporateAdmin);
        corporateSink.scheduleBroadcast(firstBroadcast, keccak256("first"));
        corporateSink.scheduleBroadcast(secondBroadcast, keccak256("second"));
        vm.stopPrank();

        vm.warp(firstBroadcast);
        vm.prank(corporateAdmin);
        corporateSink.uploadCertificate(0, keccak256("certificate"));

        assertEq(
            corporateSink.getCorporateCSR(0),
            keccak256("certificate")
        );
    }
}
