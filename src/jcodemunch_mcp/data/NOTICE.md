# Third-party notice: tiktoken

jcodemunch counts the tokens a tool response saved with the `tiktoken`
library and its `cl100k_base` encoding (`jcodemunch_mcp/savings_tokenizer.py`).
The encoding file is not shipped in this package: `tiktoken` fetches it once,
from `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken`,
the first time a count is needed, and caches it. Setting `tokenizer_download`
to false (or `JCODEMUNCH_TOKENIZER_DOWNLOAD=0`) stops the fetch. It is not used
to talk to any OpenAI service.

`tiktoken` is distributed under the MIT License:

```
MIT License

Copyright (c) 2022 OpenAI, Shantanu Jain

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
