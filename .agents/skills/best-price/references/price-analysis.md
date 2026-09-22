# Price analysis

Treat `/v1/stats` as raw, unfiltered arithmetic for one run. Always inspect `/v1/products` before
making market claims. Build comparable statistics only from deduplicated products with compatible
model/SKU, bundle, condition class, and valid asking-price meaning. Retain every excluded product
and reason for audit.

Report raw count, distinct count, comparable count, exclusions, min/P25/median/P75/max, capture
window, and partial status. With insufficient samples, report the observed range and uncertainty;
do not manufacture a fair-market price. Never mix run IDs just to increase sample size.
