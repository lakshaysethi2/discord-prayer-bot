/// <reference types="cypress" />

describe('Login Page', () => {
  beforeEach(() => {
    cy.visit('/login');
  });

  it('should display the login page title', () => {
    cy.title().should('contain', 'Login');
  });

  it('should display the login heading', () => {
    cy.contains('h2', 'Admin Login').should('be.visible');
  });

  it('should have a password input for the admin token', () => {
    cy.get('input[type="password"][name="token"]').should('be.visible');
  });

  it('should have a submit button', () => {
    cy.get('button[type="submit"]').should('be.visible')
      .and('contain', 'Login');
  });

  it('should show error on invalid token submission', () => {
    cy.get('input[name="token"]').type('wrong-token-12345');
    cy.get('button[type="submit"]').click();
    cy.contains('Invalid token').should('be.visible');
    cy.contains('a', 'Try again').should('be.visible')
      .and('have.attr', 'href', '/login');
  });

  it('should have a link to try again after failed login', () => {
    cy.get('input[name="token"]').type('bad-token');
    cy.get('button[type="submit"]').click();
    cy.contains('a', 'Try again').click();
    cy.url().should('include', '/login');
  });

  it('should render the login form with proper styling', () => {
    cy.get('form').should('be.visible');
    cy.get('form input[name="token"]').should('have.attr', 'placeholder', 'Enter ADMIN_TOKEN');
    cy.get('form input[name="token"]').should('have.attr', 'required');
  });
});
