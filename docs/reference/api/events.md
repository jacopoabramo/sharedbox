---
icon: lucide/code
---

# Events

| Name | What it does |
| --- | --- |
| [`BoxEvents`][sharedbox.BoxEvents] | the signal group of a box: one signal per field, plus `follow`, `unfollow` and `nested` |
| [`FieldWatch`][sharedbox.FieldWatch] | iterates over the values written to one field |

::: sharedbox.BoxEvents
    options:
      show_root_heading: true
      members:
        - follow
        - unfollow
        - nested

::: sharedbox.FieldWatch
    options:
      show_root_heading: true
