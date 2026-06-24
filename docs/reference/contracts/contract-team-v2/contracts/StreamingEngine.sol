// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title StreamingEngine
 * @notice 流计算引擎合约，管理UTO代币的释放流速，根据时间戳实时计算累计释放量
 */
contract StreamingEngine {
    // ============ 存储变量 ============
    
    /// @notice 基准余额，用于变轨模型的基数
    uint256 public BaseBalance;
    
    /// @notice 当前每秒释放速率（单位：wei/秒）
    uint256 public Rate;
    
    /// @notice 上一次结算的时间戳
    uint256 public CheckpointTime;
    
    /// @notice 绑定的WitnessDAO合约地址
    address public daoAddress;

    // ============ 事件 ============
    
    /// @notice 用户数更新时触发
    event UserCountUpdated(uint256 indexed newUserCount, uint256 newRate, uint256 baseBalance);
    
    /// @notice DAO地址初始化时触发
    event DAOInitialized(address indexed daoAddress);

    // ============ 修饰器 ============
    
    /// @notice 仅允许绑定的DAO合约调用
    modifier onlyDAO() {
        require(msg.sender == daoAddress, "StreamingEngine: caller is not the DAO");
        _;
    }

    // ============ 构造函数 ============
    
    /**
     * @notice 构造函数
     * @param _initialCheckpointTime 初始基准时间戳
     * @param _initialUserCount 初始见证用户数
     */
    constructor(uint256 _initialCheckpointTime, uint256 _initialUserCount) {
        CheckpointTime = _initialCheckpointTime;
        BaseBalance = 0;
        // Rate = (UserCount * 10^18) / 86400
        Rate = (_initialUserCount * 1e18) / 86400;
    }

    // ============ 外部函数 ============
    
    /**
     * @notice 设定绑定的WitnessDAO地址（仅可调用一次）
     * @param _daoAddress WitnessDAO合约地址
     */
    function initializeDAO(address _daoAddress) external {
        require(daoAddress == address(0), "StreamingEngine: DAO already initialized");
        require(_daoAddress != address(0), "StreamingEngine: invalid DAO address");
        daoAddress = _daoAddress;
        emit DAOInitialized(_daoAddress);
    }

    /**
     * @notice 更新见证用户数（仅绑定的DAO可调用）
     * @param newUserCount 新的用户数
     */
    function updateUserCount(uint256 newUserCount) external onlyDAO {
        // 1. 先结算基准余额
        BaseBalance = _computeCurrentBalance();
        // 2. 更新基准时间戳
        CheckpointTime = block.timestamp;
        // 3. 根据新用户数重新计算流速
        Rate = (newUserCount * 1e18) / 86400;
        
        emit UserCountUpdated(newUserCount, Rate, BaseBalance);
    }

    // ============ 公开视图函数 ============
    
    /**
     * @notice 获取当前实时累计释放总额
     * @return 实时累计代币总额
     */
    function getCurrentBalance() public view returns (uint256) {
        return _computeCurrentBalance();
    }

    // ============ 内部函数 ============
    
    /**
     * @notice 根据变轨模型计算实时累计总额
     * @return 计算后的代币总额
     */
    function _computeCurrentBalance() internal view returns (uint256) {
        if (block.timestamp <= CheckpointTime) {
            return BaseBalance;
        }
        return BaseBalance + ((block.timestamp - CheckpointTime) * Rate);
    }
}