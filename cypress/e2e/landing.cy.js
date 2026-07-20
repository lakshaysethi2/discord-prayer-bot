/// <reference types="cypress" />

describe('Landing Page', () => {
  beforeEach(() => {
    cy.visit('/');
  });

  it('should display the correct page title', () => {
    cy.title().should('include', 'Prayer Bot');
  });

  it('should display the main heading with emoji', () => {
    cy.contains('h1', 'Discord Prayer Bot').should('be.visible');
  });

  it('should display the tagline describing the app', () => {
    cy.contains('p', 'Schedule & recite prayers').should('be.visible');
  });

  it('should display the tradition list', () => {
    cy.contains('Buddhist').should('be.visible');
    cy.contains('Christian').should('be.visible');
    cy.contains('Jewish').should('be.visible');
    cy.contains('Sufi').should('be.visible');
    cy.contains('Vedantic').should('be.visible');
    cy.contains('Three Daily').should('be.visible');
  });

  it('should show "Bot online" status indicator', () => {
    cy.contains('Bot online').should('be.visible');
  });

  it('should have a Server Management link card', () => {
    cy.contains('a', 'Server Management').should('be.visible')
      .and('have.attr', 'href', '/servers');
  });

  it('should have a header nav with Login link (when not authenticated)', () => {
    cy.get('header nav a[href="/login"]').should('be.visible');
  });

  it('should have a header nav with Servers link', () => {
    cy.get('header nav a[href="/servers"]').should('be.visible');
  });

  it('should have a nav brand link pointing to home', () => {
    cy.get('header nav a[href="/"]').should('be.visible');
  });
});
