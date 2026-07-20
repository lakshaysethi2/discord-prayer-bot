const { defineConfig } = require('cypress');

module.exports = defineConfig({
  e2e: {
    baseUrl: 'https://prayer-bot-dnd.lak.nz',
    supportFile: 'cypress/support/e2e.js',
    specPattern: 'cypress/e2e/**/*.cy.js',
    viewportWidth: 1280,
    viewportHeight: 800,
    video: false,
    screenshotOnRunFailure: true,
    retries: {
      runMode: 1,
      openMode: 0,
    },
    env: {
      ADMIN_TOKEN: process.env.CYPRESS_ADMIN_TOKEN || 'dev-token-change-me',
      GUILD_ID: '1194598173742731284',
    },
  },
});
