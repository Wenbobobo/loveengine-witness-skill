// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {Test} from "forge-std/Test.sol";
import {SkillRegistry} from "../src/SkillRegistry.sol";

contract SkillRegistryTest is Test {
    SkillRegistry private registry;
    address private publisher = address(0xA11CE);
    address private otherPublisher = address(0xB0B);
    bytes32 private constant SKILL_ID = keccak256("loveengine-witness");
    bytes32 private constant VERSION_1 = keccak256("0.3.0-network-pilot");
    bytes32 private constant VERSION_2 = keccak256("0.3.1-network-pilot");
    bytes32 private constant PACKAGE_1 = keccak256("package-1");
    bytes32 private constant PACKAGE_2 = keccak256("package-2");
    bytes32 private constant MANIFEST_1 = keccak256("manifest-1");
    bytes32 private constant MANIFEST_2 = keccak256("manifest-2");

    function setUp() public {
        registry = new SkillRegistry();
    }

    function _publishV1() private {
        vm.prank(publisher);
        registry.publishRelease(
            SKILL_ID,
            VERSION_1,
            PACKAGE_1,
            MANIFEST_1,
            bytes32(0)
        );
    }

    function testPublisherOwnsOnlyItsNamespace() public {
        _publishV1();

        vm.prank(otherPublisher);
        registry.publishRelease(
            SKILL_ID,
            VERSION_1,
            PACKAGE_2,
            MANIFEST_2,
            bytes32(0)
        );

        SkillRegistry.Release memory first = registry.getRelease(
            publisher,
            SKILL_ID,
            VERSION_1
        );
        SkillRegistry.Release memory second = registry.getRelease(
            otherPublisher,
            SKILL_ID,
            VERSION_1
        );
        assertEq(first.packageHash, PACKAGE_1);
        assertEq(second.packageHash, PACKAGE_2);
    }

    function testRejectsInvalidAndDuplicateRelease() public {
        vm.prank(publisher);
        vm.expectRevert(SkillRegistry.InvalidRelease.selector);
        registry.publishRelease(
            SKILL_ID,
            VERSION_1,
            bytes32(0),
            MANIFEST_1,
            bytes32(0)
        );

        _publishV1();
        vm.prank(publisher);
        vm.expectRevert(SkillRegistry.ReleaseAlreadyExists.selector);
        registry.publishRelease(
            SKILL_ID,
            VERSION_1,
            PACKAGE_1,
            MANIFEST_1,
            bytes32(0)
        );
    }

    function testCurrentVersionMustBeActive() public {
        _publishV1();
        vm.prank(publisher);
        registry.setReleaseStatus(
            SKILL_ID,
            VERSION_1,
            SkillRegistry.ReleaseStatus.Deprecated,
            bytes32(0)
        );

        vm.prank(publisher);
        vm.expectRevert(SkillRegistry.ReleaseNotActive.selector);
        registry.setCurrentVersion(SKILL_ID, VERSION_1);
    }

    function testRevokedReleaseCannotBeReactivated() public {
        _publishV1();
        vm.prank(publisher);
        registry.setReleaseStatus(
            SKILL_ID,
            VERSION_1,
            SkillRegistry.ReleaseStatus.Revoked,
            bytes32(0)
        );

        vm.prank(publisher);
        vm.expectRevert(SkillRegistry.InvalidStatusTransition.selector);
        registry.setReleaseStatus(
            SKILL_ID,
            VERSION_1,
            SkillRegistry.ReleaseStatus.Active,
            bytes32(0)
        );
    }

    function testReplacementMustExistInPublisherNamespace() public {
        _publishV1();
        vm.prank(publisher);
        vm.expectRevert(SkillRegistry.ReplacementNotFound.selector);
        registry.setReleaseStatus(
            SKILL_ID,
            VERSION_1,
            SkillRegistry.ReleaseStatus.Deprecated,
            VERSION_2
        );

        vm.prank(publisher);
        registry.publishRelease(
            SKILL_ID,
            VERSION_2,
            PACKAGE_2,
            MANIFEST_2,
            VERSION_1
        );
        vm.prank(publisher);
        registry.setReleaseStatus(
            SKILL_ID,
            VERSION_1,
            SkillRegistry.ReleaseStatus.Deprecated,
            VERSION_2
        );

        SkillRegistry.Release memory release = registry.getRelease(
            publisher,
            SKILL_ID,
            VERSION_1
        );
        assertEq(
            uint256(release.status),
            uint256(SkillRegistry.ReleaseStatus.Deprecated)
        );
        assertEq(release.replacementVersionHash, VERSION_2);
    }
}
