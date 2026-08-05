#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5（P0：去hint + 全局相干形变）复测图：(1) 攻击成功率 vs 形变强度；(2) 可读性蒙太奇。"""
import json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

HERE = os.path.dirname(os.path.abspath(__file__))
FIGS = os.path.join(HERE, "figs"); os.makedirs(FIGS, exist_ok=True)
for cand in ["/System/Library/Fonts/Supplemental/Songti.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"]:
    if os.path.exists(cand):
        fm.fontManager.addfont(cand)
        try: plt.rcParams["font.family"] = fm.FontProperties(fname=cand).get_name()
        except Exception: pass
        break
plt.rcParams["axes.unicode_minus"] = False

res = json.load(open(os.path.join(HERE, "results_v5.json")))
sw = res["sweep"]; TR = res["trials"]
gw = [s["gw"] for s in sw]
oracle = [s["oracle_static"]*100 for s in sw]
bfs = [s["bf_static"]*100 for s in sw]
bfr = [s["bf_robust"]*100 for s in sw]
gid = [s["bf_robust_glyph"]*100 for s in sw]

fig, ax = plt.subplots(figsize=(7.6, 4.7))
ax.axhspan(0, 5, color="#1E8449", alpha=0.08)
ax.text(max(gw), 2.2, "理想区（攻击≈随机基线）", ha="right", va="bottom", fontsize=8.5, color="#1E8449")
ax.axhline(89.8, ls="--", lw=1.2, color="#7D3C98", label="v3 基线 89.8%（有 hint）")
ax.plot(gw, oracle, "-^", color="#95A5A6", lw=1.8, ms=7, label="oracle-hint 攻击者（知字形=v4 威胁）")
ax.plot(gw, bfs, "-o", color="#C0392B", lw=2.3, ms=8, label="bf-alphabet 攻击者（去 hint，静态）")
ax.plot(gw, bfr, "-s", color="#E67E22", lw=2.3, ms=7, label="bf-alphabet 攻击者（去 hint，鲁棒 r=2）")
ax.plot(gw, gid, ":d", color="#2980B9", lw=1.6, ms=6, label="攻击者“字形识别”率（鲁棒）")
for x, v in zip(gw, bfr): ax.annotate(f"{v:.0f}%", (x, v), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=8.5, color="#E67E22", fontweight="bold")
ax.set_xlabel("全局相干弹性形变幅度 GW (px)  —— 平滑形变，人眼仍可读", fontsize=9.5)
ax.set_ylabel("私有排列 P 端到端恢复率 (%)", fontsize=10)
ax.set_title(f"v5 复测：去 hint + 全局相干形变（{TR} 次/项，字符集 {res['alphabet']}）\n"
             "GW=0 证实“仅去 hint 无效”（穷举 13 字形即补偿）；形变才是有效杠杆", fontsize=10.5, fontweight="bold")
ax.set_ylim(-3, 100); ax.set_xticks(gw); ax.grid(alpha=0.25); ax.legend(fontsize=8, loc="upper right")
fig.tight_layout()
p1 = os.path.join(FIGS, "fig_v5_sweep.png"); fig.savefig(p1, dpi=170, bbox_inches="tight"); print("wrote", p1)

# 可读性蒙太奇
s = json.load(open(os.path.join(HERE, "sample_v5.json")))
side = s["side"]; panels = s["panels"]
amap = {sv["gw"]: sv["bf_robust"]*100 for sv in sw}
fig2, axes = plt.subplots(1, len(panels), figsize=(3.4*len(panels), 3.8))
for ax, pn in zip(axes, panels):
    board = np.array(pn["board"], dtype=np.uint8).reshape(side, side, 3)
    ax.imshow(board); ax.axis("off")
    G = pn["gw"]; rate = amap.get(G, float("nan"))
    rd = "清晰" if G == 0 else ("仍可读（平滑形变）" if G <= 16 else "强形变/偏难")
    col = "#1E8449" if G <= 16 else "#E67E22"
    ax.set_title(f"GW = {G}px\n人类可读性：{rd}\n攻击恢复率：{rate:.0f}%", fontsize=9.5, color=col)
fig2.suptitle(f"全局相干形变的“安全/可读性”权衡（字符“{panels[0]['ch']}”，按真实 P 拼合）：\n"
              "形变在整盘平滑一致，人眼凭格式塔仍能识读，而攻击者的“未形变干净模板”严重错位",
              fontsize=11, fontweight="bold", y=1.05)
fig2.tight_layout()
p2 = os.path.join(FIGS, "fig_v5_readability.png"); fig2.savefig(p2, dpi=170, bbox_inches="tight"); print("wrote", p2)
print("sweep:", [(x["gw"], round(x["bf_static"]*100,1), round(x["bf_robust"]*100,1), round(x["bf_robust_glyph"]*100,1)) for x in sw])
