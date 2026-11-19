# Core Retail Sales — Stage 1 Report

Generated: 2026-11-17T13:10:35.258226+00:00

Data: 238 events, 2007-01-12 → 2026-10-15

## Size boundaries

- Beat small: |s| ≤ 0.200 (n=103)
- Beat medium: 0.200 < |s| ≤ 0.400
- Beat large: |s| > 0.400
- Miss small: |s| ≤ 0.200 (n=110)
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
| beat small | 44 | reliable | -0.0477 | -0.0439 | 0.159 | 0.318 | 0.523 | 0.333 | 0.667 | 0.667 | 0.159 | weak |
| beat medium | 27 | reliable | -0.1649 | -0.1696 | 0.037 | 0.667 | 0.296 | 0.053 | 0.947 | 0.947 | 0.630 | strong |
| beat large | 32 | reliable | -0.1098 | -0.1042 | 0.062 | 0.469 | 0.469 | 0.118 | 0.882 | 0.882 | 0.406 | moderate |
| miss small | 39 | reliable | 0.0650 | 0.0505 | 0.385 | 0.154 | 0.462 | 0.714 | 0.286 | 0.714 | 0.231 | weak |
| miss medium | 41 | reliable | 0.1449 | 0.1258 | 0.537 | 0.098 | 0.366 | 0.846 | 0.154 | 0.846 | 0.439 | moderate |
| miss large | 28 | reliable | 0.0872 | 0.0720 | 0.393 | 0.143 | 0.464 | 0.733 | 0.267 | 0.733 | 0.250 | weak |
| inline inline | 25 | reliable | -0.0103 | 0.0010 | 0.240 | 0.240 | 0.520 | 0.500 | 0.500 | 0.500 | 0.000 | noise |


## XAU — m15 (main) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 44 | reliable | 0.0106 | 0.0376 | 0.295 | 0.250 | 0.455 | 0.542 | 0.458 | 0.542 | 0.045 | noise |
| beat medium | 27 | reliable | -0.2152 | -0.1391 | 0.037 | 0.481 | 0.481 | 0.071 | 0.929 | 0.929 | 0.444 | moderate |
| beat large | 32 | reliable | -0.0878 | -0.0422 | 0.062 | 0.312 | 0.625 | 0.167 | 0.833 | 0.833 | 0.250 | weak |
| miss small | 39 | reliable | 0.0465 | 0.0670 | 0.308 | 0.205 | 0.487 | 0.600 | 0.400 | 0.600 | 0.103 | noise |
| miss medium | 41 | reliable | 0.1550 | 0.0666 | 0.439 | 0.146 | 0.415 | 0.750 | 0.250 | 0.750 | 0.293 | weak |
| miss large | 28 | reliable | 0.1238 | 0.1059 | 0.393 | 0.179 | 0.429 | 0.688 | 0.312 | 0.688 | 0.214 | weak |
| inline inline | 25 | reliable | -0.0040 | 0.0244 | 0.200 | 0.240 | 0.560 | 0.455 | 0.545 | 0.545 | 0.040 | noise |


## XAU — m30 (secondary) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 44 | reliable | 0.0277 | 0.0407 | 0.273 | 0.205 | 0.523 | 0.571 | 0.429 | 0.571 | 0.068 | noise |
| beat medium | 27 | reliable | -0.2534 | -0.2229 | 0.111 | 0.556 | 0.333 | 0.167 | 0.833 | 0.833 | 0.444 | moderate |
| beat large | 32 | reliable | -0.0943 | -0.0581 | 0.125 | 0.312 | 0.562 | 0.286 | 0.714 | 0.714 | 0.188 | weak |
| miss small | 39 | reliable | 0.0243 | 0.0464 | 0.282 | 0.282 | 0.436 | 0.500 | 0.500 | 0.500 | 0.000 | noise |
| miss medium | 41 | reliable | 0.2052 | 0.1362 | 0.415 | 0.122 | 0.463 | 0.773 | 0.227 | 0.773 | 0.293 | weak |
| miss large | 28 | reliable | 0.1049 | 0.1195 | 0.393 | 0.143 | 0.464 | 0.733 | 0.267 | 0.733 | 0.250 | weak |
| inline inline | 25 | reliable | 0.0007 | 0.0369 | 0.200 | 0.240 | 0.560 | 0.455 | 0.545 | 0.545 | 0.040 | noise |


# DXY

## DXY — m5 (main) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| inline inline | 0 | empty | — | — | — | — | — | — | — | — | — | nan |


## DXY — m15 (main) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| inline inline | 0 | empty | — | — | — | — | — | — | — | — | — | nan |


## DXY — m30 (secondary) — все периоды

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| inline inline | 0 | empty | — | — | — | — | — | — | — | — | — | nan |


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
| beat small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| inline inline | 0 | empty | — | — | — | — | — | — | — | — | — | nan |


## P2_2013_2019

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| inline inline | 0 | empty | — | — | — | — | — | — | — | — | — | nan |


## P3_2020_2022

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| inline inline | 0 | empty | — | — | — | — | — | — | — | — | — | nan |


## P4_2023_2025

| template | n | reliability | mean | median | p_up | p_down | p_flat | p_up\|dir | p_down\|dir | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| beat small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| beat large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss small | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss medium | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| miss large | 0 | empty | — | — | — | — | — | — | — | — | — | nan |
| inline inline | 0 | empty | — | — | — | — | — | — | — | — | — | nan |


# Сводка: сценарии с рабочим сигналом (m5, all periods)

| instrument | template | n | direction | dir_acc | dir_edge | quality |
|---|---|---|---|---|---|---|
| XAU | beat medium | 27 | down | 0.947 | 0.630 | strong |
| XAU | beat large | 32 | down | 0.882 | 0.406 | moderate |
| XAU | miss medium | 41 | up | 0.846 | 0.439 | moderate |
