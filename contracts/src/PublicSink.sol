// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IStreamingEngine} from "./interfaces/IStreamingEngine.sol";

contract PublicSink {
    IStreamingEngine public immutable streamingEngine;

    constructor(address streamingEngine_) {
        streamingEngine = IStreamingEngine(streamingEngine_);
    }

    function getTotalUTO() external view returns (uint256) {
        return streamingEngine.getCurrentBalance();
    }
}
