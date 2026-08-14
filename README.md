# Miro Discord Bot

A comprehensive Discord bot with 33 systems for server management, economy, moderation, and entertainment.

## Features

### Core Systems
- **Verification**: CAPTCHA-based member verification
- **Economy**: Coins, gems, shop, daily rewards, challenges
- **Leveling**: XP system with role rewards
- **Tickets**: Private support ticket system
- **Suggestions**: Community voting on suggestions
- **Giveaways**: Automated giveaway management
- **Welcome/Leave**: Customizable join/leave messages
- **Anti-Raid**: Automatic raid detection and prevention
- **Auto-Mod**: Content moderation and filtering
- **Warnings**: User warning and punishment system
- **Reminders**: Scheduled personal reminders
- **Announcements**: Announcement management system
- **Auto-Responder**: Keyword-based automated responses
- **Reaction Roles**: Role assignment via reactions
- **Staff Shifts**: Staff shift tracking and management
- **Staff Reviews**: Staff performance evaluation
- **Starboard**: Popular message highlighting
- **AI Chat**: AI-powered chat channels
- **Modmail**: Private staff messaging
- **Logging**: Comprehensive server event logging

## Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd discord-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your bot token and settings
   ```

4. **Run the bot**
   ```bash
   python bot.py
   ```

## Configuration

1. **Invite the bot** to your server with the following permissions:
   - Send Messages
   - Use Slash Commands
   - Embed Links
   - Read Message History
   - Manage Roles
   - Manage Channels
   - Kick Members
   - Ban Members
   - Moderate Members

2. **Set up systems** using the `/autosetup` command (Administrator only)

3. **Configure individual systems** using `/configpanel <system>` (Administrator only)

## Deployment

### Railway (Recommended)
1. Connect your GitHub repository to Railway
2. Set environment variables in Railway dashboard
3. Deploy automatically

### Docker
```bash
docker build -t miro-bot .
docker run -d --env-file .env miro-bot
```

### Local/VPS
```bash
python bot.py
```

## Data Persistence

- All data is automatically saved to JSON files in the `data/` directory
- SQLite database for conversation history
- Automatic backups every 6 hours
- Zero data loss on restarts

## Support

- Use `/ticket` to create support tickets
- Check the created channels after setup
- All systems are fully functional after `/autosetup`

## License

This project is provided as-is for educational and server management purposes.