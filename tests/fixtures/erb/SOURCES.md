# ERB fixture provenance

Fixtures for the `erb` language (`tests/test_erb.py`). Every vendored file
carries the same attribution as an ERB comment header (`<%# %>`) in its own
body, following `tests/fixtures/dotnet/SOURCES.md`; the header renders nothing,
so each file is still a working template.

⚠ **Pinned by commit SHA, not by branch**, so each upstream file can be
identified byte for byte. Only MIT and BSD-licensed sources are vendored: the
larger Rails apps used to check indexing end to end (forem, redmine,
discourse) are GPL or AGPL and are never copied here.

## Vendored — `rails/rails`

**Commit:** `dcb4f3121bba525e3bb64596ef08dd1e32a3a947`
**License:** MIT (text below). Copyright (c) David Heinemeier Hansson
(actionpack, actionview); Copyright (c) 37signals LLC (actiontext).

| local | upstream | why this file |
|---|---|---|
| `rails_rescues_trace.html.erb` | `actionpack/lib/action_dispatch/middleware/templates/rescues/_trace.html.erb` | Ruby blocks spanning many tags, multi-line `<% %>`, ids that are dynamic (`id="traces-<%= … %>"`), a `<script>` whose strings hold tags, and a non-ASCII `✏️` that shifts byte offsets from character offsets |
| `rails_rescues_trace.text.erb` | `…/rescues/_trace.text.erb` | `.text` host: no parser for the host, Ruby only |
| `rails_rescues_layout.erb` | `…/rescues/layout.erb` | bare `.erb`; a layout with `yield :style` inside a `<style>` block, `yield`, a static id and `var x = function` scripts |
| `rails_fragment_cached_with_variant.html+phone.erb` | `actionpack/test/fixtures/functional_caching/formatted_fragment_cached_with_variant.html+phone.erb` | a `+variant` suffix; a block opened and closed on one line |
| `rails_messages_show.json.erb` | `actiontext/test/dummy/app/views/messages/show.json.erb` | `.json` host with tags at value and string positions |
| `rails_comments_show.js.erb` | `actionview/test/fixtures/digestor/comments/show.js.erb` | the common `.js.erb` shape: one call around `j render(…)` |
| `rails_customer.xml.erb` | `actionview/test/fixtures/customers/_customer.xml.erb` | `.xml` host |

## Vendored — `solidusio/solidus`

**Commit:** `f41bf95d44143830fe4c7d16cc57851621c07d6a`
**License:** BSD-3-Clause (text below).

| local | upstream | why this file |
|---|---|---|
| `solidus_orders_form.html.erb` | `backend/app/views/spree/admin/orders/_form.html.erb` | `render` partial calls in three spellings, and a `<script>` with a `<% each do %>` loop and a `<%==` tag inside it |
| `solidus_option_values_create.turbo_stream.erb` | `admin/app/views/solidus_admin/option_values/create.turbo_stream.erb` | a host format with no parser (`turbo_stream`) |
| `solidus_admin_application.html.erb` | `admin/app/views/layouts/solidus_admin/application.html.erb` | a real application layout: `yield`, a static `id="main"`, tags in attributes |

## Hand-written

`handwritten_*.erb` were written for these tests and cover what no small
upstream file does in one place: `content_for`/`provide`/`yield` slots, trim
tags (`<%-`, `-%>`), a literal `<%%`, a method defined inside a tag
(`handwritten_slots.html.erb`); a JavaScript host with a function and tags at a
string, expression and function-name position (`handwritten_widget.js.erb`); a
Ruby host, i.e. a generator template, with a tag at a class-name position
(`handwritten_model.rb.erb`); a YAML host (`handwritten_database.yml.erb`).

## License texts

### MIT (rails/rails)

```
Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

```

### BSD-3-Clause (solidusio/solidus)

```
Copyright © 2007-2014, Spree Commerce Inc. and other contributors.
All rights reserved.

Copyright © 2015-2026, Solidus contributors.
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
* Neither the name of Spree Commerce Inc. nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

This software is provided by the copyright holders and contributors "as is" and any express or implied warranties, including, but not limited to, the implied warranties of merchantability and fitness for a particular purpose are disclaimed. In no event shall the copyright owner of contributors be liable for any direct, indirect, incidental, special, exemplary, or consequential damages (including, but not limited to, procurement of substitute goods or services; loss of use, data, or profits; or business interruption) however caused and on any theory of liability, whether in contract, strict liability, or tort (including negligence or otherwise) arising in any way out of the use of this software, even if advised of the possibility of such damage.
```
