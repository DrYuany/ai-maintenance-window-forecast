"""护栏：只判"结构上根本做不了"，不判"这句话算不算排程"。

这是从上一版学到的教训。旧版的门 `is_planning_request` 用 30 个裸关键词判
"这句话归不归我管"，结果 `钱不超过300万` 被路由词认领、预算正则不认"钱"、
返回默认方案——起点就是那道门。

所以这里划一条硬边界：

  护栏只问「这个操作在数学上/权限上存不存在」
  不问「这个诉求该不该我处理」

"该不该我处理"由模型判（它读得懂整句），再由 A1/A10 机器核。
有判据的地方不需要正则再猜一遍——那正是"同一件事被抄三遍"的老毛病。

宽度也要克制：宁漏不误。一条假警报就足以让人关掉整个护栏，
而漏掉的那些，模型还有机会自己说"做不到"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 1) 非线性：这些进了 CP-SAT 也表达不了（线性模型）
NONLINEAR = re.compile(
    r"(?:平方|立方|开方|开根|根号|次方|倒数|方差|标准差|乘积|相乘"
    r"|(?:最大|最小)值的?(?:平方|差|比))"
)

# 2) 越界操作：动的不是排程模型，是别的东西
OFF_SCOPE = re.compile(
    r"(?:改|调|设|换|变)(?:一下|成|为)?(?:颜色|界面|字体|主题|皮肤|按钮|背景|图标)"
    r"|(?:改|动|删|清空)(?:数据库|代码|源码|服务器|配置)"
    r"|(?:重启|部署|安装)(?:一下|下)?(?:服务|服务器|系统|软件|环境)"
)

# 3) 自相矛盾：同一句里对同一对象给了互斥的取值（只需抓到最明显的）
CONTRADICTION = re.compile(r"(?:不许|不要|禁止)(?:修|排)?.{0,6}?(?:必须|一定要|务必)(?:修|排)")


@dataclass
class Hit:
    category: str
    evidence: str

    def as_dict(self) -> dict[str, str]:
        return {"category": self.category, "evidence": self.evidence}


def check(utterance: str) -> Hit | None:
    text = (utterance or "").strip()
    if not text:
        return None
    for pattern, name in ((NONLINEAR, "非线性，线性模型表达不了"),
                          (OFF_SCOPE, "越界操作，动的不是排程模型"),
                          (CONTRADICTION, "同句自相矛盾")):
        found = pattern.search(text)
        if found:
            return Hit(name, found.group(0))
    return None
