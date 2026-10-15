# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
5. **One thing you would change** if this had to serve 100x the data.

Ans1- I created a unique index on emp_code in employees and a unique compound index on (emp_code, date) in attendance logs. These ensure unique employee codes and prevent duplicate daily attendance records. I would avoid a redundant standalone emp_code index on attendance logs because the compound index already supports queries filtering by its first field.

Ans2- If two identical punch-in requests arrive simultaneously, both might pass application-level checks. However, the unique compound index allows only one record for that employee and date. The other request fails with DuplicateKeyError, which should be handled and returned as HTTP 409 Conflict.

Ans3- The leaderboard uses competition ranking, so employees with equal late minutes receive the same rank. However, if the result limit falls between tied employees, a strict limit may exclude one of them. To include everyone tied at the cutoff, additional logic is required.

Ans4- The department summary starts from the employees collection and joins attendance records using $lookup. Therefore, employees with zero attendance logs are still counted if they meet the department and joining-date criteria.

Ans5- I would cross check and optimize the analytics APIs to handle larger attendance datasets. Currently, monthly and department analytics use MongoDB aggregation pipelines, which is good, but aggregations may become slower as records grow.