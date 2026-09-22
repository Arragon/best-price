"""100 labeled synthetic cases spanning five product categories.

These are regression labels for the conservative rules baseline, not real listings or prices.
They deliberately test only explicit text evidence; cloud-model quality needs separate live gating.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisCase:
    category: str
    text: str
    expected_type: str
    expected_tag: str | None = None


def cases() -> list[AnalysisCase]:
    categories = {
        "camera": "富士 X-T4 相机",
        "cpu": "AMD 9950X3D 处理器",
        "ssd": "2TB NVMe SSD",
        "drone": "DJI Mini 无人机",
        "display": "27寸 4K 显示器",
    }
    patterns = [
        ("功能正常，个人闲置", "physical_product", None),
        ("正常低价出，支持平台交易", "physical_product", None),
        ("包装齐全，无明显问题", "physical_product", None),
        ("自用商品，描述如图", "physical_product", None),
        ("可当面测试", "physical_product", None),
        ("出租，日租价格", "rental", None),
        ("租赁一个月起", "rental", None),
        ("日租服务，不出售", "rental", None),
        ("求购一台，成色不限", "wanted", None),
        ("高价回收同型号", "wanted", None),
        ("收一台自用", "wanted", None),
        ("仅收定金，标价非售价", "deposit", "price_placeholder"),
        ("订金链接，价格不是实际价格", "deposit", "price_placeholder"),
        ("定金预留", "deposit", "price_placeholder"),
        ("偶尔黑屏，重启恢复", "physical_product", "functional_issue_claim"),
        ("不开机，当配件出", "physical_product", "functional_issue_claim"),
        ("进水后功能异常", "physical_product", "functional_issue_claim"),
        ("仅配件，不含主机", "accessory", None),
        ("原装空盒", "accessory", None),
        ("保护壳一个", "accessory", None),
    ]
    return [
        AnalysisCase(category, f"{product}，{suffix}", expected_type, expected_tag)
        for category, product in categories.items()
        for suffix, expected_type, expected_tag in patterns
    ]
