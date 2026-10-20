"""推送前敏感信息扫描：检查将被提交的文本与图片二进制。"""
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    # 只针对本机实际网段，不匹配通用 RFC1918 地址——10.0.0.0/8 之类不具识别性，
    # 测试里的 192.168.1.1 只是常量，不该报。
    r"10\.229\.",
    r"暮雨", r"muyuyanran", r"_paths", r"DeepseekHarnessProjects",
    r"C:\\\\Users", r"/c/Users", r"7897",
    r"sk-[A-Za-z0-9]{16,}", r"ghp_[A-Za-z0-9]{20,}", r"github_pat_",
    r"api[_-]?key\s*[:=]\s*['\"][^'\"]{8,}",
    r"password\s*[:=]\s*['\"][^'\"]+",
]
COMBINED = re.compile("|".join(PATTERNS), re.I)


def scan_text(path: str) -> list[str]:
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError as exc:
        return [f"读取失败 {exc}"]
    return [f"{path}: {m.group(0)!r}" for m in COMBINED.finditer(text)]


def scan_binary(path: str) -> list[str]:
    data = open(path, "rb").read()
    hits = []
    for pat in PATTERNS:
        try:
            raw = pat.encode("utf-8")
        except UnicodeEncodeError:
            continue
        if re.search(raw, data, re.I):
            hits.append(f"{path}: {pat!r}")
    return hits


def main() -> int:
    files = subprocess.run(["git", "diff", "--cached", "--name-only"],
                           capture_output=True, text=True, check=True).stdout.split()
    self_name = Path(__file__).name
    findings: list[str] = []
    checked = 0
    for f in files:
        # 规则文件本身包含这些模式字面量，扫它必然自指误报
        if Path(f).name == self_name:
            continue
        checked += 1
        low = f.lower()
        if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico")):
            findings += scan_binary(f)
        elif low.endswith((".py", ".js", ".html", ".css", ".md", ".toml", ".txt",
                           ".json", ".yml", ".yaml", ".gitignore")):
            findings += scan_text(f)
        else:
            findings += scan_binary(f)

    print(f"扫描 {checked} 个待提交文件（跳过扫描器自身 {self_name}）")
    if findings:
        print("发现问题：")
        for item in findings:
            print("  -", item)
        return 1
    print("未发现敏感信息 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
