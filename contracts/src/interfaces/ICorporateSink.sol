// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

interface ICorporateSink {
    function isProposalWindowOpen(uint256 timestamp)
        external
        view
        returns (bool);

    function recordCompensation(
        uint256 amount,
        bytes32 requestHash,
        bytes32 evidenceBundleHash
    ) external;
}
