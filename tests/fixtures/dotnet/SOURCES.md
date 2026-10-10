# .NET fixture provenance

Fixtures for ASP.NET Web Forms, WPF/XAML and MSBuild file handling.

Every vendored file also carries the same attribution as a comment header in its
own body, following the `tests/fixtures/razor/dotnet_aspnetcore_layout.cshtml`
precedent. This file exists because a per-file header does not survive the file
being copied elsewhere, and because the razor fixtures have no equivalent — a
reader there cannot tell which files are upstream and which were hand-written
without opening each one.

⚠ **Pinned by commit SHA, not by branch.** The razor precedent links `main`, which
names a different file over time; a fixture whose upstream cannot be identified
byte-for-byte is not reproducible evidence. Same reasoning as
`benchmarks/tasks.json`.

## Vendored — `microsoft/dotnet-framework-docker`

**Commit:** `1c4b3a1f86a3429648bb6af07965d00e625910cf`
**License:** MIT — Copyright (c) 2016 Microsoft Corporation
**Upstream dir:** `samples/aspnetapp/aspnetapp/`

A complete, Microsoft-maintained ASP.NET Web Forms application. Chosen over the
alternatives because it is MIT, actively maintained, and carries the whole file
family rather than a fragment.

| local | upstream | why this file |
|---|---|---|
| `webforms_default.aspx` | `Default.aspx` | `<%@ Page %>` with `CodeBehind=` + `Inherits=` + `MasterPageFile=` |
| `webforms_default.aspx.cs` | `Default.aspx.cs` | the codebehind half of the page↔class edge |
| `webforms_site.master` | `Site.Master` | master page, `ContentPlaceHolder`, `<%@ Master %>` |
| `webforms_site.master.designer.cs` | `Site.Master.designer.cs` | **reproduces the `<summary>` docstring defect** in public code — generated control fields whose XML doc comment is extracted as the literal tag |
| `webforms_viewswitcher.ascx` | `ViewSwitcher.ascx` | `<%@ Control %>` user control, `<%: %>` expressions |
| `webforms_viewswitcher.ascx.cs` | `ViewSwitcher.ascx.cs` | user-control codebehind |
| `webforms_global.asax` | `Global.asax` | `<%@ Application %>`, app lifecycle entry points |
| `webforms_web.config` | `Web.config` | handler/module/provider wiring. ⚠ Also the reason `.config` is NOT a mapped extension — see the note in `parser/languages.py`. This copy is a Microsoft sample and contains no credentials. |
| `oldstyle_aspnetapp.csproj` | `aspnetapp.csproj` | **old-style** MSBuild: explicit `<Compile Include>` + `<DependentUpon>`, which declares the page↔codebehind pairing |

## Vendored — `microsoft/WPF-Samples`

**Commit:** `428feec619ff748a7c98f53bb6f757ee7dc6b7c7`
**License:** MIT — Copyright (c) 2015 Microsoft

| local | upstream | why this file |
|---|---|---|
| `wpf_dialogbox_mainwindow.xaml` | `Windows/DialogBox/MainWindow.xaml` | `x:Class`, `Name=`, and six attribute-wired handlers (`Click=`, `Closing=`). Proof that markup-wired handler edges are **not** a Web Forms problem — XAML has the identical shape, so the resolver wants to be shared |
| `wpf_wizard_resources.resx` | `Windows/Wizard/Properties/Resources.resx` | `.resx` is the one newly-mapped extension the xml extractor handles genuinely well (named `<data>` entries become symbols) |

## Hand-written — no upstream

Unattributed by design, following `tests/fixtures/razor/Counter.razor`.

| file | why hand-written |
|---|---|
| `webforms_handlers.aspx` | **No MIT-licensed official Microsoft source exists for declarative `On*=` handler wiring.** Every MS Web Forms sample under MIT uses `AutoEventWireup` + `Page_Load` and wires nothing declaratively. The one ideal file, `microsoft/Windows-classic-samples` `Samples/Win7Samples/winui/shell/shellextensibility/OpenSearch/Default.aspx` (it has capital *and* lowercase variants side by side), is **NOASSERTION** and cannot be vendored. Searched `microsoft`/`dotnet`/`aspnet` by extension and content: of 17 repos with matching `.aspx`, 2 were MIT and both were unrelated third-party projects. |
| `webforms_handlers.aspx.cs` | codebehind for the above |

The hand-written pair deliberately encodes the cases a naive scan gets wrong, each
of which cost a real miscount during investigation:

- capital `OnClick=` (common) **and** lowercase `onclick=` (older designers)
- `OnClientClick=` — a client-side JS hook, **not** a codebehind edge, on a control
  that also carries a real `OnClick`
- `ButtonType=`, `HorizontalAlign=`, `ControlToValidate=` — attribute names
  containing `on`, whose values a scan without a word boundary captures as handler
  names
- `btnGhost_Click` — wired in markup, absent from the codebehind, so an unresolved
  edge must be reportable rather than silently counted
- `Page_Load` / `Page_Init` — referenced by nothing, bound by naming convention,
  and therefore **not** fixable by parsing `.aspx`
- `UnusedHelper()` — genuinely uncalled, so a resolver that marks everything live
  is as wrong as one that marks everything dead

## Not used, and why

| source | reason |
|---|---|
| `microsoft/Windows-classic-samples` | NOASSERTION |
| `aspnet/samples` | NOASSERTION |
| `dotnet/samples`, `dotnet/docs` | CC-BY-4.0. Redistributable with attribution, but mixing a content licence into an MIT repo is a decision nobody needs to inherit from a test fixture. Their `framework/wcf/.../WebForms/CS/client/Default.aspx` is otherwise useful — it adds `CodeFile=` (the website-project spelling of `CodeBehind=`) and `<%@ Import Namespace= %>`. |
| `microsoft/ApplicationInsights-dotnet` | MIT and usable, but `examples/ClassicAspNetWebApp/` is the same Visual Studio template as the `dotnet-framework-docker` sample. A second copy adds coverage of nothing. |

## Modifications

Vendored files are byte-identical to upstream **except**:

1. A provenance comment header, in the file's own comment syntax
   (`<%-- --%>` / `//` / `<!-- -->`).
2. Where a file opens with an XML declaration, the header is placed immediately
   after it, since `<?xml ?>` must remain the first thing in the document.
3. A UTF-8 BOM present upstream is preserved. Several of these files have one, which
   is itself worth keeping: reading them needs `encoding="utf-8-sig"`, and a fixture
   set with no BOM anywhere would never catch a decoder that mishandles it.

## License text for the vendored files

The files listed under "Vendored" above are copied from repositories Microsoft
publishes under the MIT License. That license requires this notice to travel
with every copy. The two copyright lines are each repository's own, as they
stand at the pinned commits; the text after them is the same in both:

```
The MIT License (MIT)

Copyright (c) 2016 Microsoft Corporation      (microsoft/dotnet-framework-docker, LICENSE.TXT)
Copyright (c) 2015 Microsoft                  (microsoft/WPF-Samples, LICENSE)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

It covers only those files. The rest of this repository, the hand-written
fixtures in this directory included, is under the repository's own LICENSE.
