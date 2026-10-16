---
title: Data quality
---

Filed statements are XML documents in the Ministry of Finance's structures. Each file is parsed into
one canonical model and checked against four accounting identities. A material failure quarantines
the file; it is kept and counted, never dropped or repaired.

## Files by fiscal year

```sql coverage
select * from publish.dq_coverage order by fiscal_year nulls last
```

<DataTable data={coverage} rows=all>
  <Column id=fiscal_year title="Fiscal year" fmt="####" />
  <Column id=files_stored title="Stored" />
  <Column id=parsed title="Parsed" />
  <Column id=graded_pass title="Pass" />
  <Column id=graded_warn title="Warn" />
  <Column id=graded_quarantined title="Quarantined" />
  <Column id=not_yet_mapped title="Not yet mapped" />
  <Column id=needs_pdf_tier title="Filed as PDF" />
  <Column id=quarantined_before_grading title="Refused before grading" />
  <Column id=entities title="Companies" />
  <Column id=suppressed title="Suppressed" />
</DataTable>

## Accounting identities

A check's pass rate leaves out the files it does not apply to (the cash-flow tie on a small or
micro statement, which has no cash-flow statement).

```sql checks
select * from publish.dq_checks
order by structure_version, filed_bodies, fiscal_year, "check"
```

<DataTable data={checks} rows=25 search=true>
  <Column id=structure_version title="Structure" />
  <Column id=filed_bodies title="Statements filed" />
  <Column id=fiscal_year title="Fiscal year" fmt="####" />
  <Column id=check title="Check" />
  <Column id=files title="Files" />
  <Column id=pass_rate title="Pass rate" fmt="pct1" />
  <Column id=failed_material title="Material failures" />
  <Column id=not_applicable title="Not applicable" />
  <Column id=entities title="Companies" />
  <Column id=suppressed title="Suppressed" />
</DataTable>
