/// <reference types="cypress" />

const ADMIN_TOKEN = Cypress.env('ADMIN_TOKEN');

describe('Server Management Page', () => {
  beforeEach(() => {
    cy.login(ADMIN_TOKEN);
    cy.visit('/servers');
    cy.url().should('include', '/servers');
  });

  it('should load the server management page', () => {
    cy.contains('h1', 'Server Management').should('be.visible');
  });

  it('should display server settings forms', () => {
    // Check if at least one server form is visible
    cy.get('form[action="/servers/update"]').should('have.length.at.least', 1);
  });

  it('should have voice and logging channel dropdowns', () => {
    cy.get('select[name="voice_channel_id"]').should('be.visible');
    cy.get('select[name="text_channel_id"]').should('be.visible');
    cy.get('select[name="logging_channel_id"]').should('be.visible');
  });

  it('should have bot voice selection', () => {
    cy.get('select[name="tts_voice"]').should('be.visible');
    cy.get('select[name="tts_voice"] option').should('have.length', 3);
  });

  it('should have links to schedule and history', () => {
    cy.contains('a', 'Schedule').should('be.visible');
    cy.contains('a', 'History').should('be.visible');
  });
});
