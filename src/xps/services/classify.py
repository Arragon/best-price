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

# 「机身」单独出现常是外观描述（实测「机身外观轻微使用痕迹」出现在套机挂牌里），
# 因此与「单机/裸机」这类明确的单机身措辞区分强弱。
_BODY_STRONG = ("单机身", "单机", "裸机", "主机", "本体")
_BODY_WEAK = ("机身",)

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

# 故障词做裸子串匹配会大面积误删正常整机（2026-09-22 实测：60 条里误伤 4 条）。
# 两类语境必须排除：
#   否定式 —— 「无拆修」「无拆无修无暗病无进水」是卖家在声明**没有**这些问题
#   枚举式 —— 「避免磕碰、受潮、进水等」「支持(人为损坏、进水进液…)」是租赁须知
#              与质保条款里的通用列举，不是在说本机状况
_FAULT_NEGATIONS = ("无", "没", "未", "非", "免")
_FAULT_NEGATION_WINDOW = 4
_ENUMERATION_SEPARATORS = "、，,/／·"

# 配件词只在标题开头这段「卖家自述这是什么」的范围内才算数。
# 真整机常在后面列附带清单（「配件:品牌电池2块 充电器」「全套包装配件都在送皮套」），
# 而真配件（「两块沣标…相机电池」）一定在开头就点明。
_HEADLINE_WINDOW = 40

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


def _has_affirmative_fault(normalized: str) -> bool:
    """只有**肯定陈述**的故障词才算故障。

    逐个出现位置检查：被否定词覆盖、或处在顿号/逗号枚举中的，都跳过。
    """
    for hint in _FAULT_HINTS:
        start = 0
        while (index := normalized.find(hint, start)) != -1:
            start = index + 1
            window = normalized[max(0, index - _FAULT_NEGATION_WINDOW) : index]
            if any(cue in window for cue in _FAULT_NEGATIONS):
                continue
            if index > 0 and normalized[index - 1] in _ENUMERATION_SEPARATORS:
                continue
            return True
    return False


def _accessory_in_headline(normalized: str) -> bool:
    return any(hint in normalized[:_HEADLINE_WINDOW] for hint in _ACCESSORY_HINTS)


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
    if _has_affirmative_fault(normalized):
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

    accessory_hit = _accessory_in_headline(normalized)
    residue = _strip_accessories(normalized)
    body_strong = any(hint in residue for hint in _BODY_STRONG)
    body_weak = any(hint in residue for hint in _BODY_WEAK)
    kit_hit = any(hint in residue for hint in _KIT_HINTS)

    if accessory_hit and not (body_strong or body_weak) and not kit_hit:
        flags.append(ACCESSORY_ONLY)

    if kit_hit:
        flags.append(BUNDLE)

    kind: str | None
    if kit_hit and body_strong:
        # 真冲突：「单机+套机都出」「单机身5499，套机6299」——
        # 无法确定挂牌价对应哪个配置，不硬猜
        kind = None
    elif kit_hit:
        kind = KIT
    elif body_strong or body_weak:
        kind = BODY
    else:
        kind = None

    # 配置无法从文本确定即进 review。「成色如图」这类外观措辞在二手挂牌里几乎无处不在，
    # 不单独作为升级 review 的理由，否则合格样本会被抽空。
    needs_review = False
    if kind is None and ACCESSORY_ONLY not in flags:
        flags.append(UNKNOWN_VARIANT)
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
