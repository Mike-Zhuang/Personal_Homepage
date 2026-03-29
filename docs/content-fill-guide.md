# Content Fill Guide

Use this file when you are ready to replace placeholders.

## Identity and Intro

- File: `data/site.toml`
- Keys:
  - `hero.title`
  - `hero.subtitle`
  - `about.paragraphs`

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

## Writing

- File: `data/writing.toml`
- Pattern:
  - Add or remove `[[items]]` blocks
  - Fill `title`, `outlet`, `date`, `link`

## Current Focus

- File: `data/now.toml`
- Key:
  - `items` list

## Contact

- File: `data/site.toml`
- Keys:
  - `contact.email`
  - `contact.location`

## API Base URL

- File: `config.toml`
- Key:
  - `params.apiBaseUrl`

For production behind Nginx reverse proxy, keep `apiBaseUrl` empty so the frontend uses same-origin `/api/*`.
