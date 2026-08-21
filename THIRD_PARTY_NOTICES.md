# Third-party notices

Mail-triage adapts narrow implementation patterns from these projects; it does
not vendor either project wholesale.

- `Arkya-AI/outlook-email-scanner`, commit
  `79cbd27645dbe74f9cd1b824d0324773e92c8c5d`: Outlook for Mac Accessibility
  tree discovery and visible-row metadata parsing. Mail-triage deliberately
  omits upstream row selection and `AXWebArea` body reading to avoid Outlook's
  mark-as-read-on-selection behavior. Copyright (c) 2026 Arkya AI.
- `weirdapps/outlook-access`, commit
  `33a771743ece6bc1057e17c70b5b606951c829f6`: dedicated browser-profile
  session capture, Outlook REST cookie filtering, `X-AnchorMailbox`, strict
  host validation, and one-time reauthentication. Copyright (c) 2026 Giorgos
  Marinos. Copyright (c) 2026 Dimitris Plessas.

Both sources are provided under the MIT License:

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

The optional Accessibility adapter dynamically recognizes `atomacos` when a
user has installed it separately. Mail-triage does not bundle or declare that
package because its GPLv2 license and stale/conflicting dependency constraints
require a separate informed decision.

No code is used from `Seaturtle111501/outlook-admin-bypass`. Its sole behavior
is an Android/Xposed hook that disables organizational MAM enforcement; it is
both outside this application's platform and prohibited security boundary.
