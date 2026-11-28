# Stage D — веса отчётов и вероятности XAUUSD

Stage D строит модель на основе готовых событийных CSV из Stage C. Повторно читать минутные BID/ASK-котировки не нужно.

## 1. Входные данные

Используется папка, созданная `stage_c_xauusd_analyzer_corrected.py`:

```text
output_stage_c_corrected/
  stage_c_run_manifest.json
  reports/
    core_pce_price_index_m_m_events.csv
    prelim_gdp_q_q_events.csv
    prelim_gdp_price_index_q_q_events.csv
    core_durable_goods_orders_m_m_events.csv
    durable_goods_orders_m_m_events.csv
    personal_income_m_m_events.csv
    personal_spending_m_m_events.csv
    unemployment_claims_events.csv
```

Скрипт проверяет, что манифест Stage C описывает восстановление времени публикации как `calendar date + scheduled release time`, а сами события приходятся на 08:30 `America/New_York`. Это защита от повторного использования прежних ошибочных временных меток.

## 2. Установка

```powershell
py -m pip install -r requirements_stage_d.txt
```

## 3. Построение модели и весов

```powershell
py stage_d_weighted_forecast.py `
  --stage-c-dir "E:\work\XAU_gold\mvp_tg_bot\mvp_retail\output_stage_c_corrected" `
  --output-dir "E:\work\XAU_gold\mvp_tg_bot\mvp_retail\output_stage_d"
```

Основные результаты:

- `stage_d_model.json` — модель для подключения из другого Python-скрипта.
- `stage_d_report_weights.csv` — веса каждого отчёта отдельно для 5 и 15 минут.
- `stage_d_walk_forward_predictions.csv` — прогнозы на отложенных исторических событиях.
- `stage_d_signal_correlations_5m.csv` и `stage_d_signal_correlations_15m.csv` — корреляции сигналов на совпадающих датах.
- `stage_d_summary.txt` — текстовая сводка вероятностей и весов по всем отчётам.
- `stage_d_actuals_example.json` — шаблон для фактических значений публикации.

## 4. Как строятся веса

Для каждого отчёта и горизонта отдельно выполняется хронологический walk-forward тест:

1. История сортируется по времени.
2. На ранней части истории оцениваются вероятности трёх направлений по группам Actual относительно Forecast.
3. Следующее событие прогнозируется только по данным, которые были известны до него.
4. Brier Skill Score сравнивает групповой прогноз с базовой вероятностью направления.
5. Отрицательный BSS даёт нулевой вес. Положительные значения корректируются на объём OOS-выборки и корреляцию сигналов отчёта с другими одновременно выходившими отчётами.
6. Скорректированные веса нормируются до 100% отдельно для 5 и 15 минут.

Если ни один отчёт не имеет положительного OOS skill, модель явно помечает равные веса как fallback. Это не считается доказательством предсказательной силы.

Вероятности отдельных групп сглаживаются к базовой вероятности отчёта, чтобы маленькая выборка не давала экстремальные 0% или 100%.

Для исторически совпадавших публикаций строится дополнительная ridge-softmax модель взаимодействий. Она используется только при положительном walk-forward BSS не ниже 0.01 и достаточном числе совместных исторических публикаций для числа полученных отчётов. Иначе используется взвешенная смесь отдельных исторических шаблонов.

Движение в долларах между отчётами не суммируется: рассчитывается взвешенная оценка одной реакции золота, чтобы не считать одно и то же движение несколько раз.

## 5. Расчёт вероятностей для новых Actual/Forecast

Заполнить `stage_d_actuals_example.json` значениями после публикации:

```json
{
  "Core PCE Price Index m/m": {"actual": "0.3%", "forecast": "0.3%"},
  "Prelim GDP q/q": {"actual": "3.5%", "forecast": "3.4%"},
  "Prelim GDP Price Index q/q": {"actual": "2.3%", "forecast": "2.3%"},
  "Core Durable Goods Orders m/m": {"actual": "0.5%", "forecast": "0.5%"},
  "Durable Goods Orders m/m": {"actual": "0.5%", "forecast": "1.9%"},
  "Personal Income m/m": {"actual": "0.4%", "forecast": "0.2%"},
  "Personal Spending m/m": {"actual": "0.4%", "forecast": "0.5%"},
  "Unemployment Claims": {"actual": "201K", "forecast": "203K"}
}
```

Можно передавать названия отчётов или slug. Поддерживаются значения с `%`, `K`, `M`, `B`.

Запуск расчёта:

```powershell
py stage_d_weighted_forecast.py `
  --stage-c-dir "E:\work\XAU_gold\mvp_tg_bot\mvp_retail\output_stage_c_corrected" `
  --output-dir "E:\work\XAU_gold\mvp_tg_bot\mvp_retail\output_stage_d" `
  --input-json "E:\work\XAU_gold\mvp_tg_bot\mvp_retail\output_stage_d\stage_d_actuals_example.json"
```

Получатся:

- `stage_d_prediction.json` — структурированный результат для другой программы.
- `stage_d_telegram_message.html` — готовый текст сообщения в HTML-формате Telegram.

## 6. Подключение к существующему Telegram-боту

`stage_d_weighted_forecast.py` можно импортировать как обычный Python-модуль. Пример вызова после того, как парсер календаря получил Actual и Forecast:

```python
from stage_d_weighted_forecast import (
    load_model,
    predict_from_actuals,
    format_telegram_message,
)

MODEL_PATH = r"E:\work\XAU_gold\mvp_tg_bot\mvp_retail\output_stage_d\stage_d_model.json"

model = load_model(MODEL_PATH)
actuals = {
    "Core PCE Price Index m/m": {"actual": "0.3%", "forecast": "0.3%"},
    "Prelim GDP q/q": {"actual": "3.5%", "forecast": "3.4%"},
    # Добавить остальные отчёты, когда они опубликованы.
}

prediction = predict_from_actuals(model, actuals)
message = format_telegram_message(prediction)

# Дальше передать message в уже существующий метод отправки Telegram:
# await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
```

Модель загружается один раз при запуске бота. После обновления Stage C и пересборки Stage D бот должен перечитать `stage_d_model.json`.

## 7. Важные ограничения

- Этот модуль не скачивает календарь, не отслеживает публикации и сам не отправляет сообщения. Он предоставляет расчётную функцию и готовый HTML-текст; получение фактических значений и вызов Telegram API остаются за существующим скриптом бота.
- До появления Actual невозможно определить конкретную из трёх групп. Можно посмотреть исторические вероятности всех трёх сценариев в `stage_d_summary.txt`.
- Если пришла только часть Actual/Forecast, модель рассчитывает оценку по доступным отчётам и добавляет предупреждение.
- Вероятности являются историческими оценками по выбранным данным XAUUSD и настройкам Stage C, а не гарантией реакции рынка.
- Не запускайте с `--allow-time-mismatch` на рабочих данных. Этот флаг нужен только для диагностического тестирования старых файлов с неправильной временной семантикой.
