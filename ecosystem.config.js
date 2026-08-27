module.exports = {
  apps: [
    {
      name: 'leadradar-outreach',
      script: 'run_bot.py',
      interpreter: 'python',
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '1G',
      // Exponential backoff restart delay to prevent tight restart loops during Telegram rate limits
      exp_backoff_restart_delay: 1000,
      restart_delay: 2000,
      max_restarts: 50,
      min_uptime: '10s',
      env: {
        NODE_ENV: 'production',
        DEAD_MAN_TIMEOUT_SECONDS: '300'
      }
    }
  ]
};
