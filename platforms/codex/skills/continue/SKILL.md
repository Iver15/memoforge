---
name: continue
description: Продолжить задачу Memoforge или передать ответ на открытое согласование.
---

# Memoforge codex — continue

Прочитай [протокол](../../router.md). `mf task resolve [task_id] [--workdir FOLDER]` возвращает work_dir. Абсолютный каталог передавай через --workdir, а не вместо task_id. При errors или unsupported покажи объяснение и остановись. Выполни next; если получен ответ пользователя на открытый gate, передай его с текущими step/attempt/generation по протоколу. Иначе исполняй цикл. При platform_mismatch задача продолжается в исходном хосте.
