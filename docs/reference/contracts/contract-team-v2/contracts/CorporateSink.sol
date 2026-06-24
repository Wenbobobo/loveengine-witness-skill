// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title CorporateSink
 * @notice 企业存证与补偿合约，供支付企业登记直播排期、上传直播证明哈希、记录治理层批准的成本补偿额度
 */
contract CorporateSink {
    // ============ 常量 ============

    /// @notice 两次直播排期之间的最短间隔（2周 = 1,209,600秒）
    uint256 public constant MIN_BROADCAST_INTERVAL = 1209600;

    // ============ 结构体 ============

    /// @notice 直播排期信息
    struct Broadcast {
        uint256 timestamp;        // 直播时间戳
        bytes32 certificateHash;  // 证明材料/协议哈希
        bool certificateUploaded; // 是否已上传证明
    }

    // ============ 存储变量 ============

    /// @notice 企业管理员地址
    address public admin;

    /// @notice 绑定的流计算引擎地址
    address public engine;

    /// @notice 绑定的WitnessDAO合约地址（不可修改）
    address public daoAddress;

    /// @notice 企业累计已获批的成本补偿额度
    uint256 public totalCompensation;

    /// @notice 直播排期列表
    Broadcast[] public broadcasts;

    // ============ 事件 ============

    /// @notice 成功登记直播排期时触发
    event BroadcastScheduled(uint256 indexed index, uint256 scheduledTime);

    /// @notice 成功上传证明时触发
    event CertificateUploaded(uint256 indexed index, bytes32 certificateHash);

    /// @notice 成本补偿额度增加时触发
    event CompensationAdded(uint256 amount, uint256 totalCompensation);

    // ============ 修饰器 ============

    /// @notice 仅允许企业管理员调用
    modifier onlyAdmin() {
        require(msg.sender == admin, "CorporateSink: caller is not the admin");
        _;
    }

    /// @notice 仅允许绑定的DAO合约调用
    modifier onlyDAO() {
        require(msg.sender == daoAddress, "CorporateSink: caller is not the DAO");
        _;
    }

    // ============ 构造函数 ============

    /**
     * @notice 构造函数
     * @param _admin 企业管理员地址
     * @param _engine 流计算引擎地址
     */
    constructor(address _admin, address _engine) {
        require(_admin != address(0), "CorporateSink: invalid admin address");
        require(_engine != address(0), "CorporateSink: invalid engine address");
        admin = _admin;
        engine = _engine;
    }

    // ============ 外部函数 ============

    /**
     * @notice 设定绑定的WitnessDAO合约地址（仅可调用一次）
     * @param _daoAddress WitnessDAO合约地址
     */
    function initializeDAO(address _daoAddress) external {
        require(daoAddress == address(0), "CorporateSink: DAO already initialized");
        require(_daoAddress != address(0), "CorporateSink: invalid DAO address");
        daoAddress = _daoAddress;
    }

    /**
     * @notice 登记下一次直播预告时间戳（仅管理员）
     * @param _timestamp 直播时间戳
     */
    function scheduleBroadcast(uint256 _timestamp) external onlyAdmin {
        uint256 len = broadcasts.length;
        if (len > 0) {
            Broadcast storage lastBroadcast = broadcasts[len - 1];
            require(
                _timestamp >= lastBroadcast.timestamp + MIN_BROADCAST_INTERVAL,
                "CorporateSink: broadcast interval too short"
            );
        }
        broadcasts.push(Broadcast(_timestamp, bytes32(0), false));
        emit BroadcastScheduled(broadcasts.length - 1, _timestamp);
    }

    /**
     * @notice 针对特定索引的直播排期上传证明材料或协议哈希（仅管理员）
     * @param _index 排期索引
     * @param _hash 证明材料/协议哈希
     */
    function uploadCertificate(uint256 _index, bytes32 _hash) external onlyAdmin {
        require(_index < broadcasts.length, "CorporateSink: index out of bounds");
        Broadcast storage broadcast = broadcasts[_index];
        require(block.timestamp >= broadcast.timestamp, "CorporateSink: broadcast not yet started");
        require(!broadcast.certificateUploaded, "CorporateSink: certificate already uploaded");
        broadcast.certificateHash = _hash;
        broadcast.certificateUploaded = true;
        emit CertificateUploaded(_index, _hash);
    }

    /**
     * @notice 增加企业累计已获批的成本补偿额度（仅绑定的DAO可调用）
     * @param amount 补偿金额
     */
    function addCompensation(uint256 amount) external onlyDAO {
        totalCompensation += amount;
        emit CompensationAdded(amount, totalCompensation);
    }

    // ============ 外部视图函数 ============

    /**
     * @notice 返回最新一次排期的直播时间戳
     * @return 直播时间戳，若无排期返回0
     */
    function nextBroadcastTime() external view returns (uint256) {
        if (broadcasts.length == 0) {
            return 0;
        }
        return broadcasts[broadcasts.length - 1].timestamp;
    }

    /**
     * @notice 根据排期索引查询对应的证明哈希
     * @param _index 排期索引
     * @return 证明哈希
     */
    function getCorporateCSR(uint256 _index) external view returns (bytes32) {
        require(_index < broadcasts.length, "CorporateSink: index out of bounds");
        return broadcasts[_index].certificateHash;
    }

    /**
     * @notice 返回企业累计已获批的成本补偿代币总额
     * @return 补偿总额
     */
    function getCompensation() external view returns (uint256) {
        return totalCompensation;
    }
}