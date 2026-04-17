# Content Fill Guide

Use this file when you are ready to replace placeholders.

## Identity and Intro

- File: `data/site.toml`
- Keys:
  - `hero.title`
  - `hero.subtitle`
  - `hero.badges[]`
  - `hero.quickFacts[]`
  - `about.paragraphs`

### Hero Quick Facts

- File: `data/site.toml`
- Pattern:
  - `hero.quickFacts = [{ label = "Base", value = "Shanghai" }]`
  - 建议 4 个以内，适合首屏快速扫读

### Hero Badges

- File: `data/site.toml`
- Pattern:
  - `hero.badges = ["Digital Twin Systems", "Applied AI"]`
  - 建议 2-4 个短语，避免太长

## Resume Link

- File: `data/site.toml`
- Keys:
  - `resume.enabled` -> set to `true` when you have a resume file
  - `resume.href` -> set to your resume URL or static file path

## Projects

- File: `data/projects.toml`
- Pattern:
  - Add or remove `[[items]]` blocks
  - Fill `title`, `status`, `stack`, `summary`, `link`
  - Optional fields for the homepage redesign:
    - `featured = true`
    - `year = "2025"`
    - `highlights = ["Short outcome", "Another short outcome"]`

## Writing

- File: `data/writing.toml`
- Pattern:
  - Add or remove `[[items]]` blocks
  - Fill `title`, `outlet`, `date`, `link`
  - Optional: `summary`

## Current Focus

- File: `data/now.toml`
- Key:
  - `items` list

## Contact

- File: `data/site.toml`
- Keys:
  - `contact.email`
  - `contact.location`

## Snapshot Behavior

- `education`, `achievements`, `now` 会被首页聚合成一个 `Snapshot` 模块。
- `writing.items` 为空时不会再占据完整大区块。

## API Base URL

- File: `config.toml`
- Key:
  - `params.apiBaseUrl`

For production behind Nginx reverse proxy, keep `apiBaseUrl` empty so the frontend uses same-origin `/api/*`.
