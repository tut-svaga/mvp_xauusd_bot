# Core Retail Sales — Stage 1 Report

Generated: 2026-11-16T17:54:03.457908+00:00

Data: 227 events, 2007-01-12 → 2025-12-16

## Size boundaries

- Beat small: |s| ≤ 0.200 (n=97)
- Beat medium: 0.200 < |s| ≤ 0.400
- Beat large: |s| > 0.400
- Miss small: |s| ≤ 0.200 (n=106)
- Miss medium: 0.200 < |s| ≤ 0.500
- Miss large: |s| > 0.500

## Как читать таблицы

- **p_up / p_down / p_flat** — доли по всем событиям, включая боковик.
- **p_up\|dir / p_down\|dir** — условные вероятности при `!flat`. **Это главная метрика для бота.**
- **dir_acc** = max(p_up\|dir, p_down\|dir). Показывает, насколько точно угадывается направление, когда движение вообще есть.
- **dir_edge** = max(p_up, p_down) − min(p_up, p_down). Насколько сильно выражено направление.
- **quality**: strong / moderate / weak / noise.


# XAU

## XAU — m5 (main) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 41 | reliable | -0.0572 | -0.0530 | 0.146 | 0.390 | 0.463 | 0.273 | 0.727 | 0.727 | 0.244 | weak |
| beat medium | 26 | reliable | -0.1559 | -0.1574 | 0.038 | 0.654 | 0.308 | 0.056 | 0.944 | 0.944 | 0.615 | strong |
| beat large | 30 | reliable | -0.1230 | -0.1216 | 0.033 | 0.500 | 0.467 | 0.062 | 0.938 | 0.938 | 0.467 | moderate |
| miss small | 37 | reliable | 0.0483 | 0.0505 | 0.378 | 0.189 | 0.432 | 0.667 | 0.333 | 0.667 | 0.189 | weak |
| miss medium | 39 | reliable | 0.1437 | 0.1258 | 0.538 | 0.103 | 0.359 | 0.840 | 0.160 | 0.840 | 0.436 | moderate |
| miss large | 28 | reliable | 0.0872 | 0.0720 | 0.393 | 0.143 | 0.464 | 0.733 | 0.267 | 0.733 | 0.250 | weak |
| inline inline | 24 | reliable | -0.0129 | -0.0071 | 0.292 | 0.250 | 0.458 | 0.538 | 0.462 | 0.538 | 0.042 | noise |


## XAU — m15 (main) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 41 | reliable | -0.0014 | 0.0166 | 0.268 | 0.268 | 0.463 | 0.500 | 0.500 | 0.500 | 0.000 | noise |
| beat medium | 26 | reliable | -0.2162 | -0.1368 | 0.038 | 0.462 | 0.500 | 0.077 | 0.923 | 0.923 | 0.423 | moderate |
| beat large | 30 | reliable | -0.0956 | -0.0512 | 0.067 | 0.333 | 0.600 | 0.167 | 0.833 | 0.833 | 0.267 | weak |
| miss small | 37 | reliable | 0.0431 | 0.0670 | 0.297 | 0.189 | 0.514 | 0.611 | 0.389 | 0.611 | 0.108 | noise |
| miss medium | 39 | reliable | 0.1594 | 0.0666 | 0.436 | 0.128 | 0.436 | 0.773 | 0.227 | 0.773 | 0.308 | moderate |
| miss large | 28 | reliable | 0.1238 | 0.1059 | 0.393 | 0.179 | 0.429 | 0.688 | 0.312 | 0.688 | 0.214 | weak |
| inline inline | 24 | reliable | 0.0004 | 0.0330 | 0.208 | 0.250 | 0.542 | 0.455 | 0.545 | 0.545 | 0.042 | noise |


## XAU — m30 (secondary) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 41 | reliable | 0.0283 | 0.0381 | 0.268 | 0.195 | 0.537 | 0.579 | 0.421 | 0.579 | 0.073 | noise |
| beat medium | 26 | reliable | -0.2614 | -0.2356 | 0.115 | 0.577 | 0.308 | 0.167 | 0.833 | 0.833 | 0.462 | moderate |
| beat large | 30 | reliable | -0.1054 | -0.0830 | 0.100 | 0.333 | 0.567 | 0.231 | 0.769 | 0.769 | 0.233 | weak |
| miss small | 37 | reliable | 0.0466 | 0.0698 | 0.297 | 0.270 | 0.432 | 0.524 | 0.476 | 0.524 | 0.027 | noise |
| miss medium | 39 | reliable | 0.2126 | 0.1362 | 0.410 | 0.128 | 0.462 | 0.762 | 0.238 | 0.762 | 0.282 | weak |
| miss large | 28 | reliable | 0.1049 | 0.1195 | 0.393 | 0.143 | 0.464 | 0.733 | 0.267 | 0.733 | 0.250 | weak |
| inline inline | 24 | reliable | -0.0008 | 0.0376 | 0.208 | 0.250 | 0.542 | 0.455 | 0.545 | 0.545 | 0.042 | noise |


# DXY

## DXY — m5 (main) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 38 | reliable | 0.0254 | 0.0053 | 0.237 | 0.105 | 0.658 | 0.692 | 0.308 | 0.692 | 0.132 | noise |
| beat medium | 25 | reliable | 0.0176 | 0.0050 | 0.160 | 0.080 | 0.760 | 0.667 | 0.333 | 0.667 | 0.080 | noise |
| beat large | 26 | reliable | 0.0275 | 0.0020 | 0.269 | 0.038 | 0.692 | 0.875 | 0.125 | 0.875 | 0.231 | weak |
| miss small | 33 | reliable | -0.0100 | -0.0130 | 0.121 | 0.273 | 0.606 | 0.308 | 0.692 | 0.692 | 0.152 | weak |
| miss medium | 33 | reliable | 0.0100 | 0.0071 | 0.121 | 0.182 | 0.697 | 0.400 | 0.600 | 0.600 | 0.061 | noise |
| miss large | 26 | reliable | -0.0314 | -0.0032 | 0.038 | 0.231 | 0.731 | 0.143 | 0.857 | 0.857 | 0.192 | weak |
| inline inline | 20 | reliable | 0.0056 | 0.0048 | 0.150 | 0.100 | 0.750 | 0.600 | 0.400 | 0.600 | 0.050 | noise |


## DXY — m15 (main) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 38 | reliable | 0.0343 | 0.0204 | 0.368 | 0.132 | 0.500 | 0.737 | 0.263 | 0.737 | 0.237 | weak |
| beat medium | 25 | reliable | 0.0083 | 0.0011 | 0.200 | 0.240 | 0.560 | 0.455 | 0.545 | 0.545 | 0.040 | noise |
| beat large | 26 | reliable | 0.0183 | -0.0001 | 0.192 | 0.038 | 0.769 | 0.833 | 0.167 | 0.833 | 0.154 | weak |
| miss small | 33 | reliable | -0.0018 | -0.0013 | 0.242 | 0.242 | 0.515 | 0.500 | 0.500 | 0.500 | 0.000 | noise |
| miss medium | 33 | reliable | -0.0049 | 0.0069 | 0.212 | 0.242 | 0.545 | 0.467 | 0.533 | 0.533 | 0.030 | noise |
| miss large | 26 | reliable | -0.0335 | -0.0168 | 0.115 | 0.269 | 0.615 | 0.300 | 0.700 | 0.700 | 0.154 | weak |
| inline inline | 20 | reliable | 0.0108 | 0.0133 | 0.350 | 0.100 | 0.550 | 0.778 | 0.222 | 0.778 | 0.250 | weak |


## DXY — m30 (secondary) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 38 | reliable | 0.0464 | 0.0333 | 0.395 | 0.053 | 0.553 | 0.882 | 0.118 | 0.882 | 0.342 | moderate |
| beat medium | 25 | reliable | 0.0530 | 0.0152 | 0.320 | 0.160 | 0.520 | 0.667 | 0.333 | 0.667 | 0.160 | weak |
| beat large | 26 | reliable | 0.0318 | 0.0175 | 0.231 | 0.154 | 0.615 | 0.600 | 0.400 | 0.600 | 0.077 | noise |
| miss small | 33 | reliable | 0.0045 | -0.0016 | 0.242 | 0.212 | 0.545 | 0.533 | 0.467 | 0.533 | 0.030 | noise |
| miss medium | 33 | reliable | -0.0016 | 0.0054 | 0.182 | 0.242 | 0.576 | 0.429 | 0.571 | 0.571 | 0.061 | noise |
| miss large | 26 | reliable | -0.0196 | 0.0096 | 0.269 | 0.308 | 0.423 | 0.467 | 0.533 | 0.533 | 0.038 | noise |
| inline inline | 20 | reliable | -0.0112 | -0.0048 | 0.150 | 0.200 | 0.650 | 0.429 | 0.571 | 0.571 | 0.050 | noise |


# Разбивка по периодам — XAU m5


## P1_2007_2012

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 12 | preliminary | -0.0087 | -0.0291 | 0.167 | 0.250 | 0.583 | 0.400 | 0.600 | 0.600 | 0.083 | noise |
| beat medium | 7 | reference | -0.1483 | -0.1452 | — | — | — | — | — | — | — | nan |
| beat large | 11 | preliminary | -0.1654 | -0.1720 | 0.000 | 0.636 | 0.364 | 0.000 | 1.000 | 1.000 | 0.636 | strong |
| miss small | 14 | preliminary | 0.0833 | 0.0702 | 0.429 | 0.143 | 0.429 | 0.750 | 0.250 | 0.750 | 0.286 | weak |
| miss medium | 12 | preliminary | 0.0925 | 0.0447 | 0.333 | 0.083 | 0.583 | 0.800 | 0.200 | 0.800 | 0.250 | weak |
| miss large | 8 | reference | 0.0747 | 0.0888 | — | — | — | — | — | — | — | nan |
| inline inline | 8 | reference | 0.0028 | 0.0321 | — | — | — | — | — | — | — | nan |


## P2_2013_2019

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 19 | reliable | -0.0674 | -0.0930 | 0.105 | 0.316 | 0.579 | 0.250 | 0.750 | 0.750 | 0.211 | weak |
| beat medium | 10 | preliminary | -0.1435 | -0.1127 | 0.100 | 0.500 | 0.400 | 0.167 | 0.833 | 0.833 | 0.400 | moderate |
| beat large | 4 | reference | -0.1115 | -0.1212 | — | — | — | — | — | — | — | nan |
| miss small | 15 | reliable | 0.0637 | 0.0655 | 0.267 | 0.133 | 0.600 | 0.667 | 0.333 | 0.667 | 0.133 | noise |
| miss medium | 19 | reliable | 0.2358 | 0.2836 | 0.737 | 0.105 | 0.158 | 0.875 | 0.125 | 0.875 | 0.632 | strong |
| miss large | 7 | reference | 0.2389 | 0.2232 | — | — | — | — | — | — | — | nan |
| inline inline | 9 | reference | -0.1374 | -0.1367 | — | — | — | — | — | — | — | nan |


## P3_2020_2022

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 4 | reference | -0.0423 | -0.0880 | — | — | — | — | — | — | — | nan |
| beat medium | 3 | reference | -0.1455 | -0.1211 | — | — | — | — | — | — | — | nan |
| beat large | 12 | preliminary | -0.0845 | -0.0839 | 0.167 | 0.500 | 0.333 | 0.250 | 0.750 | 0.750 | 0.333 | moderate |
| miss small | 1 | reference | 0.1813 | 0.1813 | — | — | — | — | — | — | — | nan |
| miss medium | 5 | reference | 0.0231 | 0.0284 | — | — | — | — | — | — | — | nan |
| miss large | 10 | preliminary | 0.0060 | 0.0194 | 0.300 | 0.300 | 0.400 | 0.500 | 0.500 | 0.500 | 0.000 | noise |
| inline inline | 1 | reference | 0.1096 | 0.1096 | — | — | — | — | — | — | — | nan |


## P4_2023_2025

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 6 | reference | -0.1317 | -0.1807 | — | — | — | — | — | — | — | nan |
| beat medium | 6 | reference | -0.1906 | -0.1848 | — | — | — | — | — | — | — | nan |
| beat large | 3 | reference | -0.1366 | -0.1471 | — | — | — | — | — | — | — | nan |
| miss small | 7 | reference | -0.0735 | -0.1294 | — | — | — | — | — | — | — | nan |
| miss medium | 3 | reference | -0.0332 | 0.0596 | — | — | — | — | — | — | — | nan |
| miss large | 3 | reference | 0.0375 | 0.0916 | — | — | — | — | — | — | — | nan |
| inline inline | 6 | reference | 0.1323 | 0.1093 | — | — | — | — | — | — | — | nan |


# Разбивка по периодам — DXY m5


## P1_2007_2012

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 9 | reference | 0.0038 | 0.0005 | — | — | — | — | — | — | — | nan |
| beat medium | 6 | reference | 0.0014 | 0.0075 | — | — | — | — | — | — | — | nan |
| beat large | 7 | reference | 0.0447 | -0.0001 | — | — | — | — | — | — | — | nan |
| miss small | 10 | preliminary | -0.0052 | -0.0152 | 0.200 | 0.400 | 0.400 | 0.333 | 0.667 | 0.667 | 0.200 | weak |
| miss medium | 7 | reference | 0.0126 | 0.0114 | — | — | — | — | — | — | — | nan |
| miss large | 6 | reference | -0.0066 | -0.0123 | — | — | — | — | — | — | — | nan |
| inline inline | 6 | reference | -0.0112 | 0.0088 | — | — | — | — | — | — | — | nan |


## P2_2013_2019

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 19 | reliable | 0.0408 | 0.0112 | 0.263 | 0.000 | 0.737 | 1.000 | 0.000 | 1.000 | 0.263 | weak |
| beat medium | 10 | preliminary | 0.0444 | 0.0334 | 0.200 | 0.000 | 0.800 | 1.000 | 0.000 | 1.000 | 0.200 | weak |
| beat large | 4 | reference | -0.0048 | -0.0039 | — | — | — | — | — | — | — | nan |
| miss small | 16 | reliable | -0.0307 | -0.0140 | 0.000 | 0.188 | 0.812 | 0.000 | 1.000 | 1.000 | 0.188 | weak |
| miss medium | 19 | reliable | 0.0172 | 0.0045 | 0.105 | 0.158 | 0.737 | 0.400 | 0.600 | 0.600 | 0.053 | noise |
| miss large | 7 | reference | -0.0651 | 0.0002 | — | — | — | — | — | — | — | nan |
| inline inline | 9 | reference | 0.0154 | 0.0074 | — | — | — | — | — | — | — | nan |


## P3_2020_2022

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 4 | reference | 0.0001 | -0.0082 | — | — | — | — | — | — | — | nan |
| beat medium | 3 | reference | -0.0393 | -0.0197 | — | — | — | — | — | — | — | nan |
| beat large | 12 | preliminary | 0.0237 | 0.0034 | 0.250 | 0.083 | 0.667 | 0.750 | 0.250 | 0.750 | 0.167 | weak |
| miss small | 1 | reference | 0.0156 | 0.0156 | — | — | — | — | — | — | — | nan |
| miss medium | 5 | reference | -0.0154 | 0.0140 | — | — | — | — | — | — | — | nan |
| miss large | 10 | preliminary | -0.0080 | -0.0032 | 0.000 | 0.200 | 0.800 | 0.000 | 1.000 | 1.000 | 0.200 | weak |
| inline inline | 1 | reference | -0.0640 | -0.0640 | — | — | — | — | — | — | — | nan |


## P4_2023_2025

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 6 | reference | 0.0261 | -0.0038 | — | — | — | — | — | — | — | nan |
| beat medium | 6 | reference | 0.0176 | 0.0016 | — | — | — | — | — | — | — | nan |
| beat large | 3 | reference | 0.0459 | 0.0101 | — | — | — | — | — | — | — | nan |
| miss small | 6 | reference | 0.0332 | 0.0083 | — | — | — | — | — | — | — | nan |
| miss medium | 2 | reference | -0.0030 | -0.0030 | — | — | — | — | — | — | — | nan |
| miss large | 3 | reference | -0.0808 | -0.0818 | — | — | — | — | — | — | — | nan |
| inline inline | 4 | reference | 0.0259 | 0.0175 | — | — | — | — | — | — | — | nan |


# Сводка: сценарии с рабочим сигналом (m5, all periods)

| instrument | template | n | direction | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|
| XAU | beat medium | 26 | down | 0.944 | 0.615 | strong |
| XAU | beat large | 30 | down | 0.938 | 0.467 | moderate |
| XAU | miss medium | 39 | up | 0.840 | 0.436 | moderate |
