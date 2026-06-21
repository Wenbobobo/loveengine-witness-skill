# LoveEngine Foundry contracts

Required toolchain:

- Foundry `v1.7.1`
- forge-std `v1.9.7`
- OpenZeppelin Contracts `v5.3.0`

Install ignored, pinned dependencies:

```powershell
forge install foundry-rs/forge-std@v1.9.7 --no-git
forge install OpenZeppelin/openzeppelin-contracts@v5.3.0 --no-git
```

Run:

```powershell
forge test
```

Contract source and tests are committed. `lib/`, `out/`, `cache/`, and `broadcast/` are generated locally and ignored.
