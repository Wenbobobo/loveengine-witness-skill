# LoveEngine extension interfaces

状态：`current`

公共协议依赖小型端口，不依赖具体基础设施。替换实现必须保持稳定错误码、JSON 数据契约、hash 规则和私钥边界。

## Transport

```python
class Transport(Protocol):
    async def connect(self) -> dict: ...
```

当前 adapter 是 outbound-only `RelayTransport`。未来 P2P 或直连 adapter 必须继续验证 task signature、chainId、Registry、recipient、nonce 和 deadline，不能把 transport 当作信任根。

## Signer

```python
class Signer(Protocol):
    def sign_message(self, address: str, message: bytes) -> str: ...
    def sign_typed_data(self, address: str, typed_data: dict) -> str: ...
```

当前 demo 使用 Anvil RPC signer。Clef、browser wallet 和 AA signer 必须在 Agent 进程外持有密钥；API 只返回签名。

## LiveSource

```python
class LiveSource(Protocol):
    def events(self, cursor: str | None = None) -> Iterable[dict]: ...
```

M4 实现 `FixtureLiveSource` 与 `HttpPushLiveSource`。平台 adapter 负责认证、限流和平台 cursor；核心只接受规范化 LiveEvent。

## ArtifactStore

```python
class ArtifactStore(Protocol):
    def put(self, content: bytes) -> dict: ...
    def get(self, sha256: str) -> bytes: ...
    def verify(self, sha256: str) -> bool: ...
```

默认本地 adapter 使用 `artifacts/sha256/<prefix>/<digest>`。S3/IPFS adapter 必须以 SHA-256 内容地址为权威，不得用可变 URL 替代 hash。

## MetadataStore

```python
class MetadataStore(Protocol):
    def create_session(self, value: dict) -> dict: ...
    def append_event(self, value: dict) -> dict: ...
    def save_bundle(self, value: dict) -> dict: ...
    def save_dispute(self, value: dict) -> dict: ...
    def save_review(self, value: dict) -> dict: ...
```

默认 adapter 是 SQLite。替代数据库必须维持 event sequence、event ID 和 node/dispute 唯一约束。

## ChainEventSource

```python
class ChainEventSource(Protocol):
    def poll(self, cursor: dict | None = None) -> list[dict]: ...
```

事件身份由 chainId、transaction hash 和 log index 组成。重组处理或公共 RPC adapter 不得改变该幂等键。

## Dashboard read model

Dashboard 只读取 session、events、bundle、disputes、reviews、gate 和 metrics，不返回 secret，不提供 mutation endpoint。适配器可更换展示层，但不能改变协议状态。

## Compatibility

- M3 V1 schema 与 EIP-712 domain version `1` 保持不可变。
- M4 新消息使用 V2 schema 与 domain version `2`。
- 未知 schema version 必须返回 `unsupported_schema_version`。
- public JSON 大整数使用十进制字符串。
- raw private key、mnemonic、keystore、auth token 和 access token 一律拒绝。
