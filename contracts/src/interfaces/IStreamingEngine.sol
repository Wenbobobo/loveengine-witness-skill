// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

interface IStreamingEngine {
    function updateUserCount(uint256 newUserCount) external;
    function getCurrentBalance() external view returns (uint256);
}
