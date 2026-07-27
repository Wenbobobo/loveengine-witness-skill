# LoveEngine Foundry contracts

Required toolchain:

- Foundry `v1.7.1`
- forge-std `v1.9.7` at commit
  `77041d2ce690e692d6e03cc812b57d1ddaa4d505`
- OpenZeppelin Contracts `v5.3.0` at commit
  `e4f70216d759d8e6a64144a9e1f7bbeed78e7079`

Install ignored, pinned dependencies:

```powershell
forge install foundry-rs/forge-std@77041d2ce690e692d6e03cc812b57d1ddaa4d505 --no-git
forge install OpenZeppelin/openzeppelin-contracts@e4f70216d759d8e6a64144a9e1f7bbeed78e7079 --no-git
```

Run:

```powershell
forge test
```

Contract source and tests are committed. `lib/`, `out/`, `cache/`, and `broadcast/` are generated locally and ignored.
