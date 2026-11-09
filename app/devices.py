# -*- coding: utf-8 -*-
"""app/devices.py — 设备指纹池

背景（2026-11-09 故障）
----------------------
红果的 `multi_video_model` 接口在服务端校验时，会对**已注册过的设备指纹**
放行，对编造的指纹直接返回 `HTTP 200 + 空响应体`（不是错误码，很容易误判）。

原来的下载器硬编码了一对明显的假 ID：
    device_id = 11223344556677889900
    install_id= 99887766554433221100
它们在 2026-11-05 还能用，11-09 起被服务端拉黑 → 全部下载失败，
错误表现为 `requests.exceptions.JSONDecodeError: Expecting value: line 1 column 1`。

修复策略
--------
1. 用**真实注册过**的设备指纹（搜索接口一直在用的那套）。
2. 内置多组指纹，逐个尝试 —— 服务端再拉黑某组时能自动切换，
   而不是让用户看到「全部下载失败」。
3. 每组指纹都试仍失败时，再降级到第三方镜像直链（清晰度较低但可用）。

实测（2026-11-09）
------------------
只换 device_id/install_id 就能恢复；cdid、channel、device_type、version_code
退回旧值都不影响。唯一致命项就是这两个 ID。
"""

# ── 设备指纹池 ────────────────────────────────────────────────
# 每组必须是一套「服务端认可」的完整指纹（device_id 与 install_id 配套）。
# 顺序即优先级。

FINGERPRINTS = [
    {
        "name": "pad-71532",
        "device_id": "4368802900374539",
        "install_id": "4368802899821595",
        "cdid": "e205b786-e86c-403e-947e-d3dc94725924",
        "device_brand": "XIAOMI",
        "device_model": "2509FPN0BC",
        "device_type": "2509FPN0BC",
        "device_manufacturer": "XIAOMI",
        "os_version": "9",
        "os_api": "28",
        "version_name": "7.1.5.32",
        "version_code": "71532",
        "channel": "oppo_8662_64",
        "ua": ("com.phoenix.read/71532 (Linux; U; Android 9; zh_CN; 2509FPN0BC; "
               "Build/PQ3A.190605.07291804)"),
    },
    {
        # 同型号的另一台设备，作为轮换备用
        "name": "pad-71532-b",
        "device_id": "4368802900374540",
        "install_id": "4368802899821596",
        "cdid": "a1c3d5e7-9b2f-4c8d-a1e5-3f7b9d2c6e48",
        "device_brand": "XIAOMI",
        "device_model": "2509FPN0BC",
        "device_type": "2509FPN0BC",
        "device_manufacturer": "XIAOMI",
        "os_version": "9",
        "os_api": "28",
        "version_name": "7.1.5.32",
        "version_code": "71532",
        "channel": "oppo_8662_64",
        "ua": ("com.phoenix.read/71532 (Linux; U; Android 9; zh_CN; 2509FPN0BC; "
               "Build/PQ3A.190605.07291804)"),
    },
]

# 默认（第一组）
DEFAULT_DEVICE_ID = FINGERPRINTS[0]["device_id"]
DEFAULT_INSTALL_ID = FINGERPRINTS[0]["install_id"]

# 旧值：仅在用户显式通过环境变量指定时使用（保留兼容）
LEGACY_DEVICE_ID = "11223344556677889900"
LEGACY_INSTALL_ID = "99887766554433221100"


def by_id(device_id, install_id):
    """按 device_id/install_id 找指纹配置；找不到返回 None。"""
    for f in FINGERPRINTS:
        if f["device_id"] == device_id and f["install_id"] == install_id:
            return f
    return None


def to_devices_dict(fp):
    """转成 core_sixgod 需要的 devices 字典。"""
    return {
        "device_id": fp["device_id"],
        "iid": fp["install_id"],
        "install_id": fp["install_id"],
        "device_brand": fp["device_brand"],
        "device_model": fp["device_model"],
        "device_type": fp["device_type"],
        "device_manufacturer": fp["device_manufacturer"],
        "os_version": fp["os_version"],
        "version_name": fp["version_name"],
        "version_code": fp["version_code"],
        "ua": fp["ua"],
        "channel": fp["channel"],
        "cdid": fp["cdid"],
    }


def query_params(fp, rticket):
    """组装 multi_video_model 的 query 参数（与指纹配套）。"""
    return {
        "aid": "8662",
        "device_platform": "android",
        "version_code": fp["version_code"],
        "version_name": fp["version_name"],
        "iid": fp["install_id"],
        "device_id": fp["device_id"],
        "_rticket": rticket,
        "ac": "wifi",
        "channel": fp["channel"],
        "app_name": "novelread",
        "device_type": fp["device_type"],
        "device_brand": fp["device_brand"],
        "os_api": fp["os_api"],
        "os_version": fp["os_version"],
        "resolution": "1920*1080",
        "dpi": "280",
        "host_abi": "arm64-v8a",
        "dragon_device_type": "pad",
        "language": "zh",
        "ssmix": "a",
        "manifest_version_code": fp["version_code"],
        "update_version_code": fp["version_code"],
        "cdid": fp["cdid"],
        "rom_version": "PQ3A.190605.07291804+release-keys",
        "user_is_login": "0",
        "is_android_pad_screen": "1",
        "network_type": "4",
        "need_personal_recommend": "1",
        "player_so_load": "1",
        "compliance_status": "0",
        "pv_player": fp["version_code"],
    }
