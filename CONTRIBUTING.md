# Contributing

Thank you for helping. Problem reports and ideas are welcome on the
[issues page](https://github.com/JBrady0850/nhl-daily-analytics/issues/new/choose).

## Reporting a problem

Say which tab, which device and browser, and the data date shown under the page title.
A screenshot helps. Please do not post personal information.

## Changing the code

1. Keep the app plain HTML, CSS and JavaScript with classic scripts and no build step,
   so it still opens from a downloaded copy.
2. Use only free public data sources. No keys, accounts, paid services or secrets.
   Follow each site's robots.txt and keep requests few.
3. Keep the app free of wagering, stake or chat-command features. `python -m nhlda check`
   and the tests reject them.
4. Escape every value the app writes into the page, and wrap every use of local storage
   in try and catch.
5. Run the tests before you open a pull request:

   ```
   python -m pytest -q
   python tests/ui_smoke.py
   ```

6. If you change the list of files the app loads, raise `VERSION` in `app/sw.js`.
7. Write documentation in plain language for non-technical readers.

By contributing you agree that your work is released under the MIT License.
