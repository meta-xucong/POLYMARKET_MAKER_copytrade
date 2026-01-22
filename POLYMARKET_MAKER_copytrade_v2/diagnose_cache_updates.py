#!/usr/bin/env python3
"""
诊断缓存更新频率的脚本

监控聚合器的缓存文件，实时显示每个token的更新频率
"""

import json
import time
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any

def main():
    cache_path = Path("/home/trader/polymarket_api/POLYMARKET_MAKER_copytrade_v2/POLYMARKET_MAKER_AUTO/data/ws_cache.json")

    if not cache_path.exists():
        print(f"❌ 缓存文件不存在: {cache_path}")
        return

    print("=" * 70)
    print("聚合器缓存更新频率诊断")
    print("=" * 70)
    print(f"缓存文件: {cache_path}")
    print("\n开始监控（按Ctrl+C退出）...\n")

    last_data: Dict[str, Dict[str, Any]] = {}
    stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {
        "seq_updates": 0,
        "updated_at_changes": 0,
        "total_reads": 0,
        "no_change": 0
    })
    start_time = time.time()
    last_summary = start_time

    try:
        while True:
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    tokens = data.get("tokens", {})

                for token_id, token_data in tokens.items():
                    seq = token_data.get("seq", 0)
                    updated_at = token_data.get("updated_at", 0)

                    stats[token_id]["total_reads"] += 1

                    if token_id in last_data:
                        last_seq = last_data[token_id].get("seq", 0)
                        last_updated_at = last_data[token_id].get("updated_at", 0)

                        if seq > last_seq:
                            stats[token_id]["seq_updates"] += 1
                            print(f"[SEQ+] {token_id[:20]}... seq: {last_seq} → {seq}")

                        if updated_at > last_updated_at:
                            if seq == last_seq:
                                stats[token_id]["updated_at_changes"] += 1
                                print(f"[TIME] {token_id[:20]}... updated_at变化但seq未变 (seq={seq})")

                        if seq == last_seq and updated_at == last_updated_at:
                            stats[token_id]["no_change"] += 1

                    last_data[token_id] = token_data

                # 每30秒打印一次统计摘要
                now = time.time()
                if now - last_summary >= 30:
                    elapsed = now - start_time
                    print("\n" + "=" * 70)
                    print(f"统计摘要（运行时间：{elapsed:.0f}秒）")
                    print("=" * 70)

                    # 只显示有更新的token
                    active_tokens = [
                        (tid, s) for tid, s in stats.items()
                        if s["seq_updates"] > 0 or s["updated_at_changes"] > 0
                    ]

                    if active_tokens:
                        print(f"\n活跃token数量: {len(active_tokens)} / {len(stats)}")
                        print("\n前5个最活跃的token:")
                        sorted_tokens = sorted(
                            active_tokens,
                            key=lambda x: x[1]["seq_updates"] + x[1]["updated_at_changes"],
                            reverse=True
                        )[:5]

                        for token_id, s in sorted_tokens:
                            seq_per_min = (s["seq_updates"] / elapsed) * 60
                            time_per_min = (s["updated_at_changes"] / elapsed) * 60
                            no_change_pct = (s["no_change"] / max(s["total_reads"], 1)) * 100

                            print(f"\n  Token: {token_id[:20]}...")
                            print(f"    seq更新: {s['seq_updates']} 次 ({seq_per_min:.1f}/min)")
                            print(f"    仅时间戳更新: {s['updated_at_changes']} 次 ({time_per_min:.1f}/min)")
                            print(f"    无变化读取: {s['no_change']} 次 ({no_change_pct:.1f}%)")
                    else:
                        print("\n⚠️  所有token都无更新！")

                    print("\n" + "=" * 70 + "\n")
                    last_summary = now

            except json.JSONDecodeError:
                # 文件正在写入中，跳过这次读取
                pass
            except Exception as e:
                print(f"读取错误: {e}")

            time.sleep(0.1)  # 与子进程主循环频率一致

    except KeyboardInterrupt:
        print("\n\n停止监控")
        print("=" * 70)
        print("最终统计")
        print("=" * 70)

        elapsed = time.time() - start_time
        for token_id, s in sorted(stats.items(), key=lambda x: x[1]["seq_updates"], reverse=True)[:10]:
            seq_per_min = (s["seq_updates"] / elapsed) * 60
            print(f"{token_id[:20]}...: {s['seq_updates']} seq更新 ({seq_per_min:.1f}/min)")

if __name__ == "__main__":
    main()
