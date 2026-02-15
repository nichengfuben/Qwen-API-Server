#!/usr/bin/env python3
"""
账户数据极限压缩工具

支持 [{"key": "value"}, ...] 格式，自动检测数据模式并选择最优压缩策略
"""

from __future__ import annotations

import base64
import bz2
import json
import lzma
import zlib
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


# ─────────────────────── 默认配置 ───────────────────────

DEFAULT_VAR_NAME = "A"


# ─────────────────────── 数据模式 ───────────────────────


class DataPattern(Enum):
    """数据模式分类"""
    ALL_SAME = auto()       # 所有项键值相同: {k: k}
    SAME_KEY = auto()       # 所有项共享同一个键名: {"email": v1}, {"email": v2}
    SAME_VALUE = auto()     # 所有项共享同一个值（罕见但支持）
    MIXED_SOME_SAME = auto()  # 部分键值相同，部分不同
    ALL_DIFFERENT = auto()  # 所有项键值都不同
    MULTI_KEY = auto()      # 存在多键字典


@dataclass(frozen=True)
class PatternAnalysis:
    """数据模式分析结果"""
    pattern: DataPattern
    keys: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    same_mask: list[bool] = field(default_factory=list)
    common_key: str | None = None
    common_value: str | None = None
    same_ratio: float = 0.0
    raw_accounts: list[dict[str, str]] = field(default_factory=list)


def analyze_pattern(accounts: list[dict[str, str]]) -> PatternAnalysis:
    """
    深度分析数据模式

    Args:
        accounts: 账户字典列表

    Returns:
        模式分析结果
    """
    if not accounts:
        return PatternAnalysis(DataPattern.ALL_SAME, raw_accounts=accounts)

    keys: list[str] = []
    values: list[str] = []
    same_mask: list[bool] = []
    has_multi_key = False

    for acc in accounts:
        if len(acc) != 1:
            has_multi_key = True
            break
        k, v = next(iter(acc.items()))
        keys.append(k)
        values.append(v)
        same_mask.append(k == v)

    if has_multi_key:
        return PatternAnalysis(DataPattern.MULTI_KEY, raw_accounts=accounts)

    same_count = sum(same_mask)
    total = len(accounts)
    same_ratio = same_count / total
    unique_keys = set(keys)
    unique_values = set(values)

    if same_count == total:
        return PatternAnalysis(
            DataPattern.ALL_SAME, keys, values, same_mask,
            same_ratio=1.0, raw_accounts=accounts,
        )
    if len(unique_keys) == 1:
        return PatternAnalysis(
            DataPattern.SAME_KEY, keys, values, same_mask,
            common_key=keys[0], same_ratio=same_ratio, raw_accounts=accounts,
        )
    if len(unique_values) == 1:
        return PatternAnalysis(
            DataPattern.SAME_VALUE, keys, values, same_mask,
            common_value=values[0], same_ratio=same_ratio, raw_accounts=accounts,
        )
    if 0 < same_count < total:
        return PatternAnalysis(
            DataPattern.MIXED_SOME_SAME, keys, values, same_mask,
            same_ratio=same_ratio, raw_accounts=accounts,
        )
    return PatternAnalysis(
        DataPattern.ALL_DIFFERENT, keys, values, same_mask,
        same_ratio=0.0, raw_accounts=accounts,
    )


# ─────────────────────── 编码策略 ───────────────────────


def _choose_separator(keys: list[str], values: list[str]) -> tuple[str, str]:
    """自动选择数据中未出现的最短分隔符"""
    all_data = '\n'.join(keys + values)

    rec_candidates = ['\n', '\x00', '\x01', '\x02', '|||', '###']
    fld_candidates = ['\t', '\x00', '\x01', '\x03', '|', '::']

    rec_sep = '\n'
    for c in rec_candidates:
        if c not in all_data:
            rec_sep = c
            break

    fld_sep = '\t'
    for c in fld_candidates:
        if c not in all_data and c != rec_sep:
            fld_sep = c
            break

    return rec_sep, fld_sep


@dataclass(frozen=True)
class EncodingStrategy:
    """编码策略"""
    name: str
    payload: bytes
    decode_expr: str  # {data} 为解压后字符串的占位符


def encode_all_same(analysis: PatternAnalysis) -> EncodingStrategy:
    payload = '\n'.join(analysis.keys).encode()
    decode_expr = "[{k:k}for k in {data}.split('\\n')]"
    return EncodingStrategy('all_same', payload, decode_expr)


def encode_same_key(analysis: PatternAnalysis) -> EncodingStrategy:
    ck = analysis.common_key
    payload = '\n'.join(analysis.values).encode()
    escaped_key = ck.replace("\\", "\\\\").replace("'", "\\'")
    decode_expr = f"[{{'{escaped_key}':v}}for v in {{data}}.split('\\n')]"
    return EncodingStrategy('same_key', payload, decode_expr)


def encode_same_value(analysis: PatternAnalysis) -> EncodingStrategy:
    cv = analysis.common_value
    payload = '\n'.join(analysis.keys).encode()
    escaped_val = cv.replace("\\", "\\\\").replace("'", "\\'")
    decode_expr = f"[{{k:'{escaped_val}'}}for k in {{data}}.split('\\n')]"
    return EncodingStrategy('same_value', payload, decode_expr)


def encode_paired(analysis: PatternAnalysis) -> EncodingStrategy:
    """智能键值对: 相同项只存一份"""
    rec_sep, fld_sep = _choose_separator(analysis.keys, analysis.values)
    records = [
        f"{k}{fld_sep}{v}" if k != v else k
        for k, v in zip(analysis.keys, analysis.values)
    ]
    payload = rec_sep.join(records).encode()

    esc_rec = repr(rec_sep)[1:-1]
    esc_fld = repr(fld_sep)[1:-1]
    decode_expr = (
        f"[{{p.split('{esc_fld}')[0]:p.split('{esc_fld}')[1]}}"
        f"if'{esc_fld}'in p else{{p:p}}"
        f"for p in {{data}}.split('{esc_rec}')]"
    )
    return EncodingStrategy('paired_smart', payload, decode_expr)


def encode_paired_full(analysis: PatternAnalysis) -> EncodingStrategy:
    """完整键值对: 总是存 k<sep>v"""
    rec_sep, fld_sep = _choose_separator(analysis.keys, analysis.values)
    records = [
        f"{k}{fld_sep}{v}"
        for k, v in zip(analysis.keys, analysis.values)
    ]
    payload = rec_sep.join(records).encode()

    esc_rec = repr(rec_sep)[1:-1]
    esc_fld = repr(fld_sep)[1:-1]
    decode_expr = (
        f"[{{p.split('{esc_fld}')[0]:p.split('{esc_fld}')[1]}}"
        f"for p in {{data}}.split('{esc_rec}')]"
    )
    return EncodingStrategy('paired_full', payload, decode_expr)


def encode_json_raw(analysis: PatternAnalysis) -> EncodingStrategy:
    """JSON 直压 baseline"""
    json_payload = json.dumps(
        analysis.raw_accounts, separators=(',', ':'), ensure_ascii=False,
    ).encode()
    decode_expr = "__import__('json').loads({data})"
    return EncodingStrategy('json_raw', json_payload, decode_expr)


def get_candidate_strategies(analysis: PatternAnalysis) -> list[EncodingStrategy]:
    """根据数据模式生成全部候选编码策略"""
    candidates: list[EncodingStrategy] = []

    match analysis.pattern:
        case DataPattern.ALL_SAME:
            candidates.append(encode_all_same(analysis))

        case DataPattern.SAME_KEY:
            candidates.append(encode_same_key(analysis))
            candidates.append(encode_paired(analysis))

        case DataPattern.SAME_VALUE:
            candidates.append(encode_same_value(analysis))
            candidates.append(encode_paired(analysis))

        case DataPattern.MIXED_SOME_SAME:
            candidates.append(encode_paired(analysis))
            candidates.append(encode_paired_full(analysis))

        case DataPattern.ALL_DIFFERENT:
            candidates.append(encode_paired(analysis))
            candidates.append(encode_paired_full(analysis))

        case DataPattern.MULTI_KEY:
            pass  # 只靠 json_raw

    # 所有模式都加 JSON baseline
    candidates.append(encode_json_raw(analysis))

    return candidates


# ─────────────────────── 压缩引擎 ───────────────────────


@dataclass(frozen=True)
class CompressionResult:
    """压缩结果容器"""
    algo: str
    strategy: str
    encoded: str
    decode_expr: str
    var_name: str = DEFAULT_VAR_NAME

    def with_var_name(self, name: str) -> CompressionResult:
        """返回使用新变量名的副本"""
        return CompressionResult(
            algo=self.algo,
            strategy=self.strategy,
            encoded=self.encoded,
            decode_expr=self.decode_expr,
            var_name=name,
        )

    # ── 内部：数据表达式 ──

    def _data_expr_oneline(self) -> str:
        return (
            f"__import__('{self.algo}')"
            f".decompress(__import__('base64').b64decode('{self.encoded}'))"
            f".decode()"
        )

    def _data_expr_compact(self) -> str:
        return f"z.decompress(b.b64decode('{self.encoded}')).decode()"

    def _data_expr_readable(self) -> str:
        return "_raw"

    # ── 代码生成 ──

    def oneline(self) -> str:
        data_expr = self._data_expr_oneline()
        body = self.decode_expr.replace('{data}', data_expr)
        return f"{self.var_name}={body}"

    def compact(self) -> str:
        data_expr = self._data_expr_compact()
        body = self.decode_expr.replace('{data}', data_expr)
        return f"import {self.algo} as z,base64 as b\n{self.var_name}={body}"

    def readable(self) -> str:
        body = self.decode_expr.replace('{data}', self._data_expr_readable())
        return (
            f"import {self.algo}\n"
            f"import base64\n"
            f"\n"
            f"_raw = {self.algo}.decompress(\n"
            f"    base64.b64decode('{self.encoded}')\n"
            f").decode()\n"
            f"\n"
            f"{self.var_name} = {body}\n"
        )

    # ── 长度 ──

    @property
    def oneline_size(self) -> int:
        return len(self.oneline())

    @property
    def compact_size(self) -> int:
        return len(self.compact())

    @property
    def best_size(self) -> int:
        return min(self.oneline_size, self.compact_size)

    def best_code(self) -> str:
        return self.oneline() if self.oneline_size <= self.compact_size else self.compact()


COMPRESSORS: list[tuple[str, Callable[[bytes], bytes]]] = [
    ('zlib', lambda d: zlib.compress(d, 9)),
    ('bz2', lambda d: bz2.compress(d, 9)),
    ('lzma', lambda d: lzma.compress(d, preset=9)),
]


def compress_strategy(
    strategy: EncodingStrategy,
    algo: str,
    compress_fn: Callable[[bytes], bytes],
    var_name: str = DEFAULT_VAR_NAME,
) -> CompressionResult:
    """对一个编码策略应用一种压缩算法"""
    compressed = compress_fn(strategy.payload)
    encoded = base64.b64encode(compressed).decode('ascii')
    return CompressionResult(algo, strategy.name, encoded, strategy.decode_expr, var_name)


def find_best_compression(
    accounts: list[dict[str, str]],
    var_name: str = DEFAULT_VAR_NAME,
) -> tuple[CompressionResult, PatternAnalysis, int]:
    """
    全局搜索最优 (编码策略 × 压缩算法) 组合

    Args:
        accounts: 账户字典列表
        var_name: 输出变量名

    Returns:
        (最佳压缩结果, 模式分析, 原始JSON大小)
    """
    analysis = analyze_pattern(accounts)
    original_size = len(json.dumps(accounts, separators=(',', ':')))
    strategies = get_candidate_strategies(analysis)

    all_results: list[CompressionResult] = [
        compress_strategy(strat, algo, fn, var_name)
        for strat in strategies
        for algo, fn in COMPRESSORS
    ]

    best = min(all_results, key=lambda r: r.best_size)
    return best, analysis, original_size


# ─────────────────────── 输出与验证 ───────────────────────


def print_comparison(
    accounts: list[dict[str, str]],
    var_name: str = DEFAULT_VAR_NAME,
) -> None:
    """打印所有压缩方案的详细比较"""
    analysis = analyze_pattern(accounts)
    original_size = len(json.dumps(accounts, separators=(',', ':')))
    strategies = get_candidate_strategies(analysis)

    print(f"数据模式: {analysis.pattern.name}")
    print(f"相同比例: {analysis.same_ratio:.1%}")
    print(f"原始大小: {original_size} 字符, {len(accounts)} 条记录")
    print(f"变量名:   {var_name}")
    print()

    header = f"{'策略':<16} {'算法':<6} {'单行':<8} {'双行':<8} {'最佳':<8} {'压缩率':<8}"
    print(header)
    print("-" * len(header))

    results: list[CompressionResult] = []
    for strat in strategies:
        for algo, fn in COMPRESSORS:
            results.append(compress_strategy(strat, algo, fn, var_name))

    results.sort(key=lambda r: r.best_size)
    best_size = results[0].best_size

    for result in results:
        ratio = result.best_size / original_size * 100
        marker = " ★" if result.best_size == best_size else ""
        print(
            f"{result.strategy:<16} {result.algo:<6} "
            f"{result.oneline_size:<8} {result.compact_size:<8} "
            f"{result.best_size:<8} {ratio:.1f}%{marker}"
        )


def verify_code(
    code: str,
    expected: list[dict[str, str]],
    var_name: str = DEFAULT_VAR_NAME,
) -> bool:
    """验证生成的代码是否正确"""
    namespace: dict = {}
    try:
        exec(code, namespace)  # noqa: S102
    except Exception as e:
        print(f"    执行错误: {e}")
        return False
    return namespace.get(var_name) == expected


def main() -> None:
    """主入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="账户数据极限压缩工具")
    parser.add_argument(
        "input", nargs="?", default=None,
        help="JSON 文件路径（省略则从 qwen_accounts 导入）",
    )
    parser.add_argument(
        "-n", "--var-name", default=DEFAULT_VAR_NAME,
        help=f"输出变量名（默认: {DEFAULT_VAR_NAME}）",
    )
    args = parser.parse_args()

    var_name: str = args.var_name

    if args.input:
        with open(args.input) as f:
            accounts = json.load(f)
    else:
        try:
            from qwen_accounts import ACCOUNTS as accounts
        except ImportError:
            print("用法: python compress_accounts.py [accounts.json] [-n VAR_NAME]")
            sys.exit(1)

    print("=" * 70)
    print("  账户数据极限压缩工具")
    print("=" * 70)
    print()

    print_comparison(accounts, var_name)
    print()

    best, analysis, original_size = find_best_compression(accounts, var_name)

    print("=" * 70)
    print(f"  最佳: {best.strategy} + {best.algo}")
    print(f"  变量: {best.var_name}")
    print(f"  大小: {best.best_size} 字符 (原始 {original_size})")
    print(f"  压缩率: {best.best_size / original_size * 100:.1f}%")
    print(f"  节省: {original_size - best.best_size} 字符")
    print("=" * 70)

    print()
    print("─── 最优代码 ───")
    print(best.best_code())

    print()
    print("─── 单行版 ───")
    print(best.oneline())

    print()
    print("─── 双行版 ───")
    print(best.compact())

    print()
    print("─── 可读版 ───")
    print(best.readable())

    print()
    print("验证中...")
    for label, code in [
        ("最优", best.best_code()),
        ("单行", best.oneline()),
        ("双行", best.compact()),
    ]:
        ok = verify_code(code, accounts, var_name)
        status = "✓ 通过" if ok else "✗ 失败"
        print(f"  {label}: {status}")

    print()
    print("完成 ✓")


if __name__ == "__main__":
    main()
