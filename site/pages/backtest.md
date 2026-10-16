---
title: Backtest
---

Every model is evaluated out of time: for each test year, it is trained only on outcomes settled
before that year began, and scored on the year itself. A pooled row gathers the test years whose
model trained on enough events. Calibration (the Brier score) is shown beside discrimination (AUC),
never AUC alone. Intervals are 95% percentile intervals over companies resampled.

<Alert status="warning">

**Purposive lists, not evidence.** With a few dozen distressed companies at most, every interval is
wide and no model's ranking here is a claim about the population. A cell with too few events shows
no metric.

</Alert>

```sql metrics
select
  horizon_months, run, model, scored_rows, test_events, entities, evaluable,
  brier, brier_low, brier_high, auc, auc_low, auc_high, suppressed
from publish.backtest_metrics
order by horizon_months, run, model
```

## 12 months

<DataTable data={metrics.where(`horizon_months = 12`)} rows=all groupBy=run>
  <Column id=model title="Model" />
  <Column id=scored_rows title="Scored rows" />
  <Column id=test_events title="Events" />
  <Column id=entities title="Companies" />
  <Column id=brier title="Brier" fmt="0.000" />
  <Column id=brier_low title="Brier low" fmt="0.000" />
  <Column id=brier_high title="Brier high" fmt="0.000" />
  <Column id=auc title="AUC" fmt="0.000" />
  <Column id=auc_low title="AUC low" fmt="0.000" />
  <Column id=auc_high title="AUC high" fmt="0.000" />
</DataTable>

## 24 months

<DataTable data={metrics.where(`horizon_months = 24`)} rows=all groupBy=run>
  <Column id=model title="Model" />
  <Column id=scored_rows title="Scored rows" />
  <Column id=test_events title="Events" />
  <Column id=entities title="Companies" />
  <Column id=brier title="Brier" fmt="0.000" />
  <Column id=brier_low title="Brier low" fmt="0.000" />
  <Column id=brier_high title="Brier high" fmt="0.000" />
  <Column id=auc title="AUC" fmt="0.000" />
  <Column id=auc_low title="AUC low" fmt="0.000" />
  <Column id=auc_high title="AUC high" fmt="0.000" />
</DataTable>

## Reliability, pooled (main run)

For each probability band, the mean predicted probability beside the share of rows that went on to
an event. A band covering too few companies shows no figures.

```sql reliability
select
  horizon_months, model, bin_low, bin_high, rows, distress_rows, mean_predicted, observed_rate,
  entities, suppressed
from publish.backtest_reliability
where run = 'main'
order by horizon_months, model, bin_low
```

<DataTable data={reliability} rows=all groupBy=model>
  <Column id=horizon_months title="Horizon (months)" />
  <Column id=bin_low title="From" fmt="0.0" />
  <Column id=bin_high title="To" fmt="0.0" />
  <Column id=rows title="Rows" />
  <Column id=mean_predicted title="Mean predicted" fmt="0.000" />
  <Column id=observed_rate title="Observed" fmt="0.000" />
  <Column id=entities title="Companies" />
  <Column id=suppressed title="Suppressed" />
</DataTable>
