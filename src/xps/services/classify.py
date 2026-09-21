"""商品相关性分类：解释性规则，可追溯，不靠大模型无证据删数据。

标签体系来自指南 §7 的 9 个，另加 4 个有实测/结构依据的：
    rental_or_lease  两轮真实搜索都出现租赁盘（¥90、¥50），是最大价格污染源
    auction          实测 schema 有 exContent.isAuction，起拍价非在售报价
    promoted_ad      实测 schema 有 exContent.isAliMaMaAD，广告位非自然结果
    model_mismatch   型号 token 明确不匹配时有文本证据，不该塞进 review 稀释样本

核心防误删设计：先把配件复合词从标题里剥掉，再在残余文本上找机身/套机线索。
否则「X-T4 机身盖」会因为含「机身」被误判为整机，而真实整机标题
「单机+原厂配件+原厂电池」又会被误判为配件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EXACT_MODEL = "exact_model"
COMPATIBLE_VARIANT = "compatible_variant"
BUNDLE = "bundle"
ACCESSORY_ONLY = "accessory_only"
REPAIR_OR_FAULT = "repair_or_fault"
WANTED_TO_BUY = "wanted_to_buy"
DEPOSIT_OR_PLACEHOLDER = "deposit_or_placeholder"
SUSPICIOUS_PRICE = "suspicious_price"
UNKNOWN_VARIANT = "unknown_variant"
RENTAL_OR_LEASE = "rental_or_lease"
AUCTION = "auction"
PROMOTED_AD = "promoted_ad"
MODEL_MISMATCH = "model_mismatch"
INVALID_IDENTITY = "invalid_identity"

BODY = "body"
KIT = "kit"
ANY = "any"

ITEM_KIND_MISMATCH = "item_kind_mismatch"

# 命中即排除，不进合格样本
_HARD_EXCLUDES = frozenset(
    {
        ACCESSORY_ONLY,
        REPAIR_OR_FAULT,
        WANTED_TO_BUY,
        RENTAL_OR_LEASE,
        AUCTION,
        PROMOTED_AD,
        DEPOSIT_OR_PLACEHOLDER,
        MODEL_MISMATCH,
        INVALID_IDENTITY,
        SUSPICIOUS_PRICE,
    }
)

# 配件复合词，长词优先剥离，避免「机身盖」被「机身」抢先命中
_ACCESSORY_HINTS = (
    "机身盖",
    "镜头盖",
    "遮光罩",
    "转接环",
    "存储卡",
    "读卡器",
    "三脚架",
    "独脚架",
    "相机包",
    "收纳包",
    "保护膜",
    "快门线",
    "充电器",
    "说明书",
    "包装盒",
    "贴膜",
    "肩带",
    "背带",
    "皮套",
    "电池",
    "手柄",
    "配件",
    "镜头",
    "滤镜",
)

_BODY_HINTS = ("单机身", "单机", "机身", "主机", "裸机", "本体")

# 只认明确的套机措辞。「18-55」这类焦段本身是镜头标记，单出镜头时会造成误判，故不列入。
_KIT_HINTS = ("套机", "套装", "双镜头", "套镜头", "KIT")

_FAULT_HINTS = (
    "进水",
    "不开机",
    "无法开机",
    "开不了机",
    "不充电",
    "故障",
    "有修",
    "拆修",
    "维修过",
    "问题机",
    "配件机",
    "尸体",
    "花屏",
    "发霉",
    "起雾",
    "摔过",
    "屏裂",
    "坏了",
)

_RENTAL_HINTS = ("租赁", "出租", "日租", "月租", "免押", "跟拍", "租")

_WANTED_PHRASES = (
    "求购",
    "求收",
    "回收",
    "收购",
    "想收",
    "想买",
    "高价收",
    "诚意收",
    "长期收",
)
# 「收 X-T4」是求购；「收藏级」「收纳包」不是。
_WANTED_LEADING = re.compile(r"^收(?![纳录获益得回藏款货件])")

_DEPOSIT_HINTS = ("定金", "订金", "押金", "占位", "补差价", "差价", "专拍", "一元", "1元")

_VARIANT_HINTS = ("银色", "黑色", "白色", "灰色", "国行", "日版", "港版", "美版", "官翻")

# 卖家把规格推给图片时，不得由模型把图片内容当已核实事实（指南 §7）
_IMAGE_DEFERRAL_HINTS = ("详情见图", "详见图", "见图", "看图", "如图", "图片为准")

# 只删连字符类分隔符，保留空格作为词边界。
# 若把空格也删掉，「富士 X-T4 18-55 套机」会黏成「富士XT41855套机」，
# 型号尾部的防碰撞断言 (?![0-9]) 就会把真正的 X-T4 一起挡掉。
_JOINERS = re.compile(r"[-_.]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ModelSpec:
    """从搜索关键词推导的型号匹配依据。"""

    keyword: str
    model_tokens: tuple[str, ...]
    brand_tokens: tuple[str, ...]


@dataclass(frozen=True)
class Classification:
    flags: tuple[str, ...]
    item_kind: str | None
    excluded: bool
    exclusion_reasons: tuple[str, ...]
    needs_review: bool


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", _JOINERS.sub("", text or "")).strip().upper()


def build_model_spec(keyword: str) -> ModelSpec:
    """含数字的 token 视为型号，其余视为品牌。

    「富士 X-T4」→ model=("XT4",) brand=("富士",)；「9950X3D」「2TB NVMe SSD」同理。
    """
    models: list[str] = []
    brands: list[str] = []
    for raw in re.split(r"[\s/,，、+]+", (keyword or "").strip()):
        token = _normalize(raw)
        if not token:
            continue
        (models if any(char.isdigit() for char in token) else brands).append(token)
    return ModelSpec((keyword or "").strip(), tuple(models), tuple(brands))


def _model_patterns(tokens: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    # 尾部负向断言防止 XT4 命中 X-T40
    return tuple(re.compile(re.escape(token) + r"(?![0-9])") for token in tokens)


def _strip_accessories(normalized: str) -> str:
    residue = normalized
    for hint in sorted(_ACCESSORY_HINTS, key=len, reverse=True):
        residue = residue.replace(hint, "")
    return residue


def classify(
    title: str,
    *,
    spec: ModelSpec,
    is_auction: bool = False,
    is_ad: bool = False,
    item_kind: str = ANY,
) -> Classification:
    normalized = _normalize(title or "")
    if not normalized:
        # 标题缺失是数据质量问题，不是型号错配，交给 review 而非静默排除
        return Classification((UNKNOWN_VARIANT,), None, False, (), True)

    flags: list[str] = []

    if is_auction:
        flags.append(AUCTION)
    if is_ad:
        flags.append(PROMOTED_AD)
    if any(hint in normalized for hint in _RENTAL_HINTS):
        flags.append(RENTAL_OR_LEASE)
    if _WANTED_LEADING.search(normalized) or any(
        phrase in normalized for phrase in _WANTED_PHRASES
    ):
        flags.append(WANTED_TO_BUY)
    if any(hint in normalized for hint in _FAULT_HINTS):
        flags.append(REPAIR_OR_FAULT)
    if any(hint in normalized for hint in _DEPOSIT_HINTS):
        flags.append(DEPOSIT_OR_PLACEHOLDER)

    if spec.model_tokens:
        if any(pattern.search(normalized) for pattern in _model_patterns(spec.model_tokens)):
            flags.append(EXACT_MODEL)
            if any(hint in normalized for hint in _VARIANT_HINTS):
                flags.append(COMPATIBLE_VARIANT)
        else:
            flags.append(MODEL_MISMATCH)

    accessory_hit = any(hint in normalized for hint in _ACCESSORY_HINTS)
    residue = _strip_accessories(normalized)
    body_hit = any(hint in residue for hint in _BODY_HINTS)
    kit_hit = any(hint in residue for hint in _KIT_HINTS)

    if accessory_hit and not body_hit and not kit_hit:
        flags.append(ACCESSORY_ONLY)

    if kit_hit:
        flags.append(BUNDLE)

    kind: str | None
    if body_hit and kit_hit:
        kind = None
    elif body_hit:
        kind = BODY
    elif kit_hit:
        kind = KIT
    else:
        kind = None

    needs_review = False
    if kind is None and ACCESSORY_ONLY not in flags:
        flags.append(UNKNOWN_VARIANT)
        needs_review = True
    if any(hint in normalized for hint in _IMAGE_DEFERRAL_HINTS):
        needs_review = True

    excluded = any(flag in _HARD_EXCLUDES for flag in flags)
    reasons = sorted({flag for flag in flags if flag in _HARD_EXCLUDES})

    if not excluded and kind is not None and item_kind != ANY and kind != item_kind:
        excluded = True
        reasons = sorted(set(reasons) | {ITEM_KIND_MISMATCH})

    return Classification(
        flags=tuple(dict.fromkeys(flags)),
        item_kind=kind,
        excluded=excluded,
        exclusion_reasons=tuple(reasons),
        needs_review=needs_review and not excluded,
    )
