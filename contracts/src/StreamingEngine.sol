// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

contract StreamingEngine {
    error NotBootstrapper();
    error WitnessDAOAlreadySet();
    error NotWitnessDAO();

    address public immutable bootstrapper;
    uint256 public immutable ratePerUser;
    address public witnessDAO;

    uint256 public checkpointTime;
    uint256 public baseBalance;
    uint256 public rate;

    event WitnessDAOSet(address indexed witnessDAO);
    event UserCountUpdated(
        uint256 newUserCount,
        uint256 newRate,
        uint256 checkpointTime,
        uint256 baseBalance
    );

    constructor(uint256 ratePerUser_, uint256 initialUserCount) {
        bootstrapper = msg.sender;
        ratePerUser = ratePerUser_;
        checkpointTime = block.timestamp;
        rate = initialUserCount * ratePerUser_;
    }

    function setWitnessDAO(address witnessDAO_) external {
        if (msg.sender != bootstrapper) revert NotBootstrapper();
        if (witnessDAO != address(0)) revert WitnessDAOAlreadySet();
        if (witnessDAO_ == address(0)) revert NotWitnessDAO();
        witnessDAO = witnessDAO_;
        emit WitnessDAOSet(witnessDAO_);
    }

    function getCurrentBalance() public view returns (uint256) {
        return baseBalance + (block.timestamp - checkpointTime) * rate;
    }

    function updateUserCount(uint256 newUserCount) external {
        if (msg.sender != witnessDAO) revert NotWitnessDAO();
        baseBalance = getCurrentBalance();
        checkpointTime = block.timestamp;
        rate = newUserCount * ratePerUser;
        emit UserCountUpdated(
            newUserCount,
            rate,
            checkpointTime,
            baseBalance
        );
    }
}
