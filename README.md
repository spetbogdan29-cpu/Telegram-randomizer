# Telegram Randomizer Bot

A Telegram bot that stores a separate list of names for each Telegram user and randomly selects one or multiple winners.

## Features
- Add names separated by commas or new lines
- View your saved list
- Pick one random person
- Pick multiple distinct winners
- Delete a name by its list number
- Clear your list with confirmation
- PostgreSQL persistence

## Deploy
1. Create a PostgreSQL database with a provider such as Neon.
2. Create a Render Web Service from this repository.
3. Set these environment variables in Render:
   - `BOT_TOKEN`: token from @BotFather
   - `DATABASE_URL`: PostgreSQL connection string from your database provider
   - `WEBHOOK_SECRET`: a random secret containing only letters, digits, underscores, or hyphens
4. Deploy. Render sets `RENDER_EXTERNAL_URL` automatically; the bot configures its Telegram webhook on startup.

Note: Render's free web services may sleep when idle, so continuous 24/7 availability is not guaranteed on the free plan.
