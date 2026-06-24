// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title WitnessDAO
 * @notice 治理合约，负责见证者的准入与注册、治理提案的发起、见证者投票签名的收集与验证、共识判定及触发下游合约的执行
 */
contract WitnessDAO {
    // ============ 枚举 ============

    /// @notice 提案类型
    enum ProposalType { UserCount, Compensation }

    // ============ 常量 ============

    /// @notice 提案通过所需最低投票总人数
    uint256 public constant MIN_VOTES_REQUIRED = 69;

    /// @notice 提案投票有效窗口期（24小时）
    uint256 public constant VOTING_PERIOD = 86400;

    /// @notice 提案创建的时间限制：只能在最新一次直播启动后的2小时内发起
    uint256 public constant BROADCAST_WINDOW = 7200;

    /// @notice Gas 退款的基础开销（用于冲抵转账与清算开销）
    uint256 public constant BASE_OVERHEAD = 25000;

    // ============ 结构体 ============

    /// @notice 注册签名结构
    struct RegisterSignature {
        address witness;
        uint8 v;
        bytes32 r;
        bytes32 s;
    }

    /// @notice 投票签名结构
    struct VoteSignature {
        address witness;
        bool support;
        uint8 v;
        bytes32 r;
        bytes32 s;
    }

    /// @notice 提案结构
    struct Proposal {
        ProposalType pType;     // 提案类型
        uint256 value;          // 提案值（用户数或补偿金额）
        uint256 startTime;      // 提案发起时间
        uint256 votesFor;       // 赞成票数
        uint256 votesAgainst;   // 反对票数
        bool executed;          // 是否已执行
        uint256 totalVoted;     // 已投票总人数（加速查询）
        mapping(uint256 => bool) voterIndexed; // witness的mapping位置索引
        // 由于mapping不能在struct中遍历，使用外部mapping记录投票状态
    }

    // ============ 存储变量 ============

    /// @notice 流计算引擎合约地址
    address public engine;

    /// @notice 企业存证合约地址
    address public corporateSink;

    /// @notice 企业管理员地址
    address public admin;

    /// @notice EIP-712 DOMAIN_SEPARATOR
    bytes32 public DOMAIN_SEPARATOR;

    /// @notice 注册签名的类型哈希
    bytes32 public constant REGISTER_TYPEHASH = keccak256("Register(address witness)");

    /// @notice 投票签名的类型哈希
    bytes32 public constant VOTE_TYPEHASH = keccak256("Vote(bool support,uint256 proposalId)");

    /// @notice 见证者注册状态（地址 → 是否已注册）
    mapping(address => bool) public isWitnessRegistered;

    /// @notice 见证者总数
    uint256 public witnessCount;

    /// @notice Relayer 白名单
    mapping(address => bool) public relayerWhitelist;

    /// @notice 提案列表
    Proposal[] public proposals;

    /// @notice 当前活跃提案ID（0 = 无活跃提案）
    uint256 public activeProposalId;

    /// @notice 见证者对提案的投票记录（proposalId → witness → 是否已投票）
    mapping(uint256 => mapping(address => bool)) public hasVoted;

    // ============ 事件 ============

    /// @notice 见证者注册成功
    event WitnessRegistered(address indexed witness);

    /// @notice 管理员发起提案
    event ProposalCreated(uint256 indexed id, ProposalType pType, uint256 value);

    /// @notice 记录单次有效投票
    event Voted(uint256 indexed id, address indexed voter, bool support);

    /// @notice 提案共识达标并自动执行
    event ProposalExecuted(uint256 indexed id, ProposalType pType, uint256 value);

    /// @notice 中继白名单更新
    event RelayerWhitelistUpdated(address indexed relayer, bool status);

    /// @notice Gas 费退还
    event GasRefunded(address indexed relayer, uint256 amount);

    // ============ 修饰器 ============

    modifier onlyAdmin() {
        require(msg.sender == admin, "WitnessDAO: caller is not the admin");
        _;
    }

    // ============ 构造函数 ============

    /**
     * @notice 构造函数
     * @param _engine 流计算引擎地址
     * @param _corporateSink 企业存证合约地址
     * @param _admin 企业管理员地址
     */
    constructor(address _engine, address _corporateSink, address _admin) {
        require(_engine != address(0), "WitnessDAO: invalid engine address");
        require(_corporateSink != address(0), "WitnessDAO: invalid corporateSink address");
        require(_admin != address(0), "WitnessDAO: invalid admin address");
        engine = _engine;
        corporateSink = _corporateSink;
        admin = _admin;
        // 初始化 EIP-712 DOMAIN_SEPARATOR
        DOMAIN_SEPARATOR = _buildDomainSeparator();
    }

    // ============ ETH接收 ============

    /// @notice 接收 ETH，用于 Gas 退款
    receive() external payable {}

    // ============ 管理员函数 ============

    /**
     * @notice 添加或移除 Relayer 白名单（仅管理员）
     */
    function setRelayerWhitelist(address _relayer, bool _status) external onlyAdmin {
        relayerWhitelist[_relayer] = _status;
        emit RelayerWhitelistUpdated(_relayer, _status);
    }

    // ============ EIP-712 函数 ============

    /**
     * @notice 构造 EIP-712 Domain Separator
     */
    function _buildDomainSeparator() internal view returns (bytes32) {
        return keccak256(abi.encode(
            keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256(bytes("WitnessDAO")),
            keccak256(bytes("1")),
            block.chainid,
            address(this)
        ));
    }

    /**
     * @notice 获取 EIP-712 Domain Separator
     */
    function getDomainSeparator() external view returns (bytes32) {
        return DOMAIN_SEPARATOR;
    }

    // ============ 见证者注册 ============

    /**
     * @notice 批量验证并注册见证者
     * @param sigs 注册签名数组
     */
    function batchRegister(RegisterSignature[] calldata sigs) external {
        uint256 startGas = gasleft();

        for (uint256 i = 0; i < sigs.length; i++) {
            RegisterSignature calldata sig = sigs[i];

            // 验证 EIP-712 签名
            bytes32 digest = keccak256(abi.encodePacked(
                "\x19\x01",
                DOMAIN_SEPARATOR,
                keccak256(abi.encode(REGISTER_TYPEHASH, sig.witness))
            ));
            address recovered = ecrecover(digest, sig.v, sig.r, sig.s);
            require(recovered == sig.witness, "WitnessDAO: invalid register signature");

            // 如果尚未注册，进行注册
            if (!isWitnessRegistered[sig.witness]) {
                isWitnessRegistered[sig.witness] = true;
                witnessCount++;
                emit WitnessRegistered(sig.witness);
            }
        }

        // Gas 退款
        _refundGas(startGas);
    }

    // ============ 提案函数 ============

    /**
     * @notice 发起"更新用户数"提案（仅管理员，需在直播窗口期内）
     * @param newUserCount 新的用户数
     */
    function proposeUserCount(uint256 newUserCount) external onlyAdmin {
        _checkBroadcastWindow();
        _createProposal(ProposalType.UserCount, newUserCount);
    }

    /**
     * @notice 发起"企业成本补偿"提案（仅管理员，需在直播窗口期内）
     * @param amount 补偿金额
     */
    function proposeCompensation(uint256 amount) external onlyAdmin {
        _checkBroadcastWindow();
        _createProposal(ProposalType.Compensation, amount);
    }

    /**
     * @notice 检查是否在直播窗口期内
     */
    function _checkBroadcastWindow() internal view {
        // 通过 CorporateSink 获取最新直播时间
        (bool success, bytes memory data) = corporateSink.staticcall(
            abi.encodeWithSignature("nextBroadcastTime()")
        );
        require(success, "WitnessDAO: failed to query broadcast time");
        uint256 lastBroadcastTime = abi.decode(data, (uint256));
        require(lastBroadcastTime > 0, "WitnessDAO: no broadcast scheduled");
        require(
            block.timestamp >= lastBroadcastTime,
            "WitnessDAO: broadcast not yet started"
        );
        require(
            block.timestamp <= lastBroadcastTime + BROADCAST_WINDOW,
            "WitnessDAO: outside broadcast window"
        );
    }

    /**
     * @notice 创建提案
     */
    function _createProposal(ProposalType _pType, uint256 _value) internal {
        // 如果存在旧的活跃提案，先关闭
        activeProposalId = proposals.length;
        proposals.push();
        Proposal storage p = proposals[activeProposalId];
        p.pType = _pType;
        p.value = _value;
        p.startTime = block.timestamp;
        p.votesFor = 0;
        p.votesAgainst = 0;
        p.executed = false;
        emit ProposalCreated(activeProposalId, _pType, _value);
    }

    // ============ 投票函数 ============

    /**
     * @notice 批量提交见证者对当前最新激活提案的投票
     * @param sigs 投票签名数组
     */
    function batchVote(VoteSignature[] calldata sigs) external {
        uint256 startGas = gasleft();

        require(activeProposalId > 0 || proposals.length > 0, "WitnessDAO: no active proposal");

        // 查找当前活跃提案（最新的未过期、未执行的提案）
        uint256 pid = activeProposalId;
        Proposal storage proposal = proposals[pid];

        // 检查提案是否过期
        require(
            block.timestamp <= proposal.startTime + VOTING_PERIOD,
            "WitnessDAO: voting period has ended"
        );

        for (uint256 i = 0; i < sigs.length; i++) {
            VoteSignature calldata sig = sigs[i];

            // 校验签名人是否为已注册见证者
            require(isWitnessRegistered[sig.witness], "WitnessDAO: witness not registered");

            // 校验是否已投过票
            if (hasVoted[pid][sig.witness]) {
                continue; // 已投票则跳过
            }

            // 验证 EIP-712 签名
            bytes32 digest = keccak256(abi.encodePacked(
                "\x19\x01",
                DOMAIN_SEPARATOR,
                keccak256(abi.encode(VOTE_TYPEHASH, sig.support, pid))
            ));
            address recovered = ecrecover(digest, sig.v, sig.r, sig.s);
            require(recovered == sig.witness, "WitnessDAO: invalid vote signature");

            // 标记已投票
            hasVoted[pid][sig.witness] = true;

            if (sig.support) {
                proposal.votesFor++;
            } else {
                proposal.votesAgainst++;
            }

            emit Voted(pid, sig.witness, sig.support);

            // 自动决议检查
            _checkAndExecute(pid);
        }

        // Gas 退款
        _refundGas(startGas);
    }

    /**
     * @notice 检查提案是否达到共识门槛并执行
     * @param pid 提案ID
     */
    function _checkAndExecute(uint256 pid) internal {
        Proposal storage proposal = proposals[pid];

        if (proposal.executed) {
            return;
        }

        uint256 totalVotes = proposal.votesFor + proposal.votesAgainst;

        if (totalVotes < MIN_VOTES_REQUIRED) {
            return;
        }

        // 共识判断：赞成票 * 10 >= 总票数 * 9（90% 门槛）
        if (proposal.votesFor * 10 >= totalVotes * 9) {
            proposal.executed = true;

            if (proposal.pType == ProposalType.UserCount) {
                // 触发 StreamingEngine.updateUserCount
                (bool success,) = engine.call(
                    abi.encodeWithSignature("updateUserCount(uint256)", proposal.value)
                );
                require(success, "WitnessDAO: failed to update user count");
            } else if (proposal.pType == ProposalType.Compensation) {
                // 触发 CorporateSink.addCompensation
                (bool success,) = corporateSink.call(
                    abi.encodeWithSignature("addCompensation(uint256)", proposal.value)
                );
                require(success, "WitnessDAO: failed to add compensation");
            }

            // 关闭活跃提案
            activeProposalId = 0;

            emit ProposalExecuted(pid, proposal.pType, proposal.value);
        }
    }

    // ============ Gas 退款 ============

    /**
     * @notice 对符合白名单的 Relayer 进行 Gas 退款
     * @param startGas 开始时的 gas 余量
     */
    function _refundGas(uint256 startGas) internal {
        if (!relayerWhitelist[msg.sender]) {
            return;
        }

        uint256 gasUsed = startGas - gasleft() + BASE_OVERHEAD;
        uint256 refundAmount = gasUsed * tx.gasprice;

        // 余额不足时安全跳过
        if (address(this).balance >= refundAmount) {
            (bool success,) = payable(msg.sender).call{value: refundAmount}("");
            if (success) {
                emit GasRefunded(msg.sender, refundAmount);
            }
        }
    }
}