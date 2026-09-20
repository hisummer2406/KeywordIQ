## Why

发布后需要从应用日志快速确认当前运行版本。现有启动完成日志不包含版本号，排查时只能再查
Git 或镜像状态。

## What Changes

- 在应用启动完成日志中输出已加载的应用版本。

## Capabilities

### New Capabilities
- `startup-version-log`: 应用启动日志标示当前应用版本。

### Modified Capabilities

- 无。

## Impact

- 影响 `main.py` 的启动日志，接口响应和业务行为不变。
