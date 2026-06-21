// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

contract SkillRegistry {
    enum ReleaseStatus {
        None,
        Active,
        Deprecated,
        Revoked
    }

    struct Release {
        bytes32 packageHash;
        bytes32 manifestHash;
        bytes32 previousVersionHash;
        bytes32 replacementVersionHash;
        ReleaseStatus status;
        uint64 publishedAt;
    }

    error InvalidRelease();
    error ReleaseAlreadyExists();
    error ReleaseNotFound();
    error ReleaseNotActive();
    error InvalidStatusTransition();
    error ReplacementNotFound();

    mapping(address publisher => mapping(bytes32 skillId => mapping(bytes32 versionHash => Release)))
        private releases;
    mapping(address publisher => mapping(bytes32 skillId => bytes32 versionHash))
        public currentVersion;

    event ReleasePublished(
        address indexed publisher,
        bytes32 indexed skillId,
        bytes32 indexed versionHash,
        bytes32 packageHash,
        bytes32 manifestHash,
        bytes32 previousVersionHash
    );
    event CurrentVersionSet(
        address indexed publisher,
        bytes32 indexed skillId,
        bytes32 indexed versionHash
    );
    event ReleaseStatusChanged(
        address indexed publisher,
        bytes32 indexed skillId,
        bytes32 indexed versionHash,
        ReleaseStatus status,
        bytes32 replacementVersionHash
    );

    function publishRelease(
        bytes32 skillId,
        bytes32 versionHash,
        bytes32 packageHash,
        bytes32 manifestHash,
        bytes32 previousVersionHash
    ) external {
        if (
            skillId == bytes32(0) ||
            versionHash == bytes32(0) ||
            packageHash == bytes32(0) ||
            manifestHash == bytes32(0)
        ) revert InvalidRelease();
        Release storage release = releases[msg.sender][skillId][versionHash];
        if (release.status != ReleaseStatus.None) revert ReleaseAlreadyExists();
        if (
            previousVersionHash != bytes32(0) &&
            releases[msg.sender][skillId][previousVersionHash].status ==
            ReleaseStatus.None
        ) revert ReleaseNotFound();

        releases[msg.sender][skillId][versionHash] = Release({
            packageHash: packageHash,
            manifestHash: manifestHash,
            previousVersionHash: previousVersionHash,
            replacementVersionHash: bytes32(0),
            status: ReleaseStatus.Active,
            publishedAt: uint64(block.timestamp)
        });
        emit ReleasePublished(
            msg.sender,
            skillId,
            versionHash,
            packageHash,
            manifestHash,
            previousVersionHash
        );
    }

    function setCurrentVersion(
        bytes32 skillId,
        bytes32 versionHash
    ) external {
        Release storage release = releases[msg.sender][skillId][versionHash];
        if (release.status != ReleaseStatus.Active) revert ReleaseNotActive();
        currentVersion[msg.sender][skillId] = versionHash;
        emit CurrentVersionSet(msg.sender, skillId, versionHash);
    }

    function setReleaseStatus(
        bytes32 skillId,
        bytes32 versionHash,
        ReleaseStatus status,
        bytes32 replacementVersionHash
    ) external {
        Release storage release = releases[msg.sender][skillId][versionHash];
        if (release.status == ReleaseStatus.None) revert ReleaseNotFound();
        if (
            status == ReleaseStatus.None ||
            (
                release.status == ReleaseStatus.Revoked &&
                status != ReleaseStatus.Revoked
            )
        ) revert InvalidStatusTransition();
        if (
            replacementVersionHash != bytes32(0) &&
            releases[msg.sender][skillId][replacementVersionHash].status ==
            ReleaseStatus.None
        ) revert ReplacementNotFound();

        release.status = status;
        release.replacementVersionHash = replacementVersionHash;
        if (
            status != ReleaseStatus.Active &&
            currentVersion[msg.sender][skillId] == versionHash
        ) {
            currentVersion[msg.sender][skillId] = bytes32(0);
        }
        emit ReleaseStatusChanged(
            msg.sender,
            skillId,
            versionHash,
            status,
            replacementVersionHash
        );
    }

    function getRelease(
        address publisher,
        bytes32 skillId,
        bytes32 versionHash
    ) external view returns (Release memory) {
        return releases[publisher][skillId][versionHash];
    }
}
