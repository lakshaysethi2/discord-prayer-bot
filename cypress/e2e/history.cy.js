/// <reference types="cypress" />

const GUILD_ID = Cypress.env('GUILD_ID');
const ADMIN_TOKEN = Cypress.env('ADMIN_TOKEN');

describe('Activity History Page', () => {
  beforeEach(() => {
    cy.login(ADMIN_TOKEN);
    cy.visit(`/history/${GUILD_ID}`);
    cy.url().should('include', `/history/${GUILD_ID}`);
  });

  it('should load the history page', () => {
    cy.contains('h1', 'Activity History').should('be.visible');
    cy.contains('Prayer Recitations').should('be.visible');
    cy.contains('Voice Room Activity').should('be.visible');
  });

  it('should display timezone info', () => {
    cy.contains('Showing times in your local timezone').should('be.visible');
  });

  it('should have a working user filter', () => {
    cy.get('#user-filter').should('be.visible');
    // Check if dropdown has options (at least "All Users")
    cy.get('#user-filter option').should('have.length.at.least', 1);
  });

  it('should navigate back to schedule', () => {
    cy.contains('Back to Schedule').click();
    cy.url().should('include', `/prayers/${GUILD_ID}`);
  });
});
