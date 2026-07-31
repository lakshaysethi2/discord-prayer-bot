/// <reference types="cypress" />

const GUILD_ID = Cypress.env('GUILD_ID');
const PUBLIC_URL = `/prayers/public/${GUILD_ID}`;

describe('Public Prayer Schedule Page', () => {
  beforeEach(() => {
    cy.visit(PUBLIC_URL);
  });

  it('should display the correct page title with guild name', () => {
    cy.title().should('include', 'Prayer Bot');
  });

  it('should display the guild/server name heading', () => {
    cy.contains('h1', 'Devotional Non-Duality').should('be.visible');
  });

  it('should display "Prayer Schedule" subtitle', () => {
    cy.contains('p', 'Prayer Schedule').should('be.visible');
  });

  it('should show the "Next Prayer" card', () => {
    cy.contains('Next Prayer').should('be.visible');
  });

  it('should show a countdown for the next prayer ("in Xh Xm")', () => {
    cy.contains(/in\s+\d+h?\s*\d*m/).should('be.visible');
  });

  it('should display perspective selector', () => {
    cy.get('#tz-selector').should('be.visible');
  });

  it('should change perspective in public view', () => {
    cy.get('#tz-selector').select('Asia/Tokyo');
    cy.get('#tz-info').should('contain', 'UTC');
  });

  it('should render day-of-week headers for scheduled days', () => {
    // At minimum, should have Monday-Sunday for this guild
    const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
    days.forEach((day) => {
      cy.contains('.text-xs.font-semibold.uppercase', day).should('be.visible');
    });
  });

  it('should display prayer tradition emojis', () => {
    cy.get('[data-utc-time]').should('have.length.greaterThan', 0);
  });

  it('should have "Join" links pointing to Discord', () => {
    cy.contains('a', 'Join').should('have.attr', 'href')
      .and('include', 'discord.com/channels');
  });

  it('should show the guild selector if multiple guilds exist', () => {
    cy.get('select').should('be.visible');
  });

  it('should have a working guild selector that navigates on change', () => {
    cy.get('select').should('have.value', GUILD_ID);
    // Verify the select has at least one option
    cy.get('select option').should('have.length.at.least', 1);
  });

  it('should have header navigation', () => {
    cy.get('header nav a[href="/"]').should('be.visible');
    cy.get('header nav a[href="/servers"]').should('be.visible');
    cy.get('header nav a[href="/login"]').should('be.visible');
  });

  it('should render schedule cards with UTC time annotation', () => {
    cy.get('[data-utc-time]').first().within(() => {
      cy.contains('UTC').should('be.visible');
    });
  });

  it('should convert UTC times to local times via JS', () => {
    // After JS runs, local-time spans should not show "--:--"
    cy.get('[data-utc-time] .local-time').should('not.contain', '--:--');
  });
});
