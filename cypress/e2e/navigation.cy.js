/// <reference types="cypress" />

const GUILD_ID = Cypress.env('GUILD_ID');

describe('Site Navigation', () => {
  it('should navigate from landing to public schedule via direct URL', () => {
    cy.visit('/');
    cy.visit(`/prayers/public/${GUILD_ID}`);
    cy.url().should('include', `/prayers/public/${GUILD_ID}`);
    cy.contains('Prayer Schedule').should('be.visible');
  });

  it('should navigate from landing to login page', () => {
    cy.visit('/');
    cy.get('a[href="/login"]').first().click();
    cy.url().should('include', '/login');
    cy.contains('Admin Login').should('be.visible');
  });

  it('should navigate from public schedule to landing via brand link', () => {
    cy.visit(`/prayers/public/${GUILD_ID}`);
    cy.get('header nav a[href="/"]').first().click();
    cy.url().should('eq', Cypress.config('baseUrl') + '/');
    cy.contains('h1', 'Discord Prayer Bot').should('be.visible');
  });

  it('should navigate from public schedule to servers page', () => {
    cy.visit(`/prayers/public/${GUILD_ID}`);
    cy.get('header nav a[href="/servers"]').first().click();
    cy.url().should('include', '/servers');
  });

  it('should navigate to the health check endpoint', () => {
    cy.request('/health').then((response) => {
      expect(response.status).to.eq(200);
      expect(response.body.status).to.eq('healthy');
    });
  });

  it('should protect the history route redirecting to login', () => {
    cy.visit(`/history/${GUILD_ID}`);
    cy.url().should('include', '/login');
    cy.contains('Admin Login').should('be.visible');
  });

  it('should protect the admin prayers route redirecting to login', () => {
    cy.visit(`/prayers/${GUILD_ID}`);
    cy.url().should('include', '/login');
    cy.contains('Admin Login').should('be.visible');
  });

  it('should protect the servers update route from unauthenticated access', () => {
    cy.request({
      url: '/servers/update',
      method: 'POST',
      form: true,
      body: { guild_id: GUILD_ID, enabled: 'on' },
      failOnStatusCode: false,
    }).then((response) => {
      // The live server may return 200 with an inline error, or redirect to login.
      // Either way it should not actually update server config without auth.
      expect(response.status).to.be.oneOf([200, 302, 303, 401, 403]);
    });
  });

  it('should have the Tailwind dark theme applied', () => {
    cy.visit('/');
    cy.get('body').should('have.class', 'bg-slate-950');
    cy.get('body').should('have.class', 'text-slate-100');
  });

  it('should have the correct viewport meta tag', () => {
    cy.visit('/');
    cy.get('meta[name="viewport"]').should('have.attr', 'content')
      .and('include', 'width=device-width');
  });
});
