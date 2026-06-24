// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/**
 * @title PublicSink
 * @notice 公共账本代理合约，向公众提供统一的只读入口查询UTO代币累计释放总额
 */
contract PublicSink {
    /// @notice 绑定的流计算引擎地址
    address public engine;

    /**
     * @notice 构造函数
     * @param _engine 流计算引擎地址
     */
    constructor(address _engine) {
        require(_engine != address(0), "PublicSink: invalid engine address");
        engine = _engine;
    }

    /**
     * @notice 查询当前的UTO释放总额
     * @return 实时UTO代币总额
     */
    function getTotalUTO() external view returns (uint256) {
        (bool success, bytes memory data) = engine.staticcall(
            abi.encodeWithSignature("getCurrentBalance()")
        );
        require(success, "PublicSink: failed to query engine");
        return abi.decode(data, (uint256));
    }
}