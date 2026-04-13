# 🚶 Walk Tracker

Hệ thống theo dõi đi bộ tự động: **Notion → GitHub Actions → Web Dashboard**

```
┌──────────┐    22:00 hàng ngày    ┌──────────────┐    auto-commit    ┌──────────────┐
│  Notion   │ ──────────────────► │ GitHub       │ ────────────────► │ GitHub Pages │
│ (ghi tay) │    Python script    │ Actions      │   data.json      │ (dashboard)  │
└──────────┘                      └──────┬───────┘                   └──────────────┘
                                         │
                                         ▼
                                   ┌───────────┐
                                   │  Notion   │
                                   │ (tổng kết)│
                                   └───────────┘
```

## 🚀 Setup (5 phút)

### 1. Tạo GitHub repo

[github.com/new](https://github.com/new) → tên `walk-tracker` → **Private** → Create

### 2. Upload files

```
walk_tracker.py
docs/index.html
.github/workflows/daily.yml
```

### 3. Thêm Secrets

Repo → **Settings** → **Secrets and variables** → **Actions**:

| Secret | Value |
|--------|-------|
| `NOTION_TOKEN` | Token của Notion Integration |
| `NOTION_PAGE_ID` | ID trang Notion (từ URL) |

Optional variable: `USER_HEIGHT` = `1.67`

### 4. Bật GitHub Pages

Settings → **Pages** → Source: **Deploy from a branch** → Branch: `main` → Folder: `/docs` → Save

### 5. Kết nối Notion

Mở trang Notion → **⋯** → **Connections** → thêm Integration

### 6. Test

Actions → **Walk Tracker Daily** → **Run workflow**

## 📖 Format dữ liệu trong Notion

Tạo **code block** (Plain text):

```
08/04
109:31 7.25 376

07/04
78:27 5.09 266
52:12 3.01 164
```

- Dòng ngày: `dd/mm` hoặc `dd/mm/yyyy` (tự detect năm)
- Dòng data: 3 số — thời gian (phút:giây), km, calo — **thứ tự tùy ý**
- Một ngày có thể nhiều buổi

## 💰 Chi phí

**Miễn phí.** GitHub Actions ~15 phút/tháng (quota: 2000 phút/tháng).
