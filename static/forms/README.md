# Blank application form

Place a blank application PDF here as:

```
static/forms/dsl-application.pdf
```

The customer portal serves this file from `GET /v1/applications/{id}/form/blank` after the National ID is approved.

The mapper in `src/egyid/form_map.py` looks on page 1 for Arabic labels:

- الإسم / الاسم
- رقم إثبات الشخصية / الرقم القومي
- ذكر / أنثى (selection marks, left = female, right = male)
- رقم الهاتف المحمول
- السرعة (30 / 70 / 100 Mbps)

Do not commit completed (filled) forms or documents that contain personal information.
