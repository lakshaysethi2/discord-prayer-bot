/// <reference types="cypress" />

// Login via cy.session() - Cypress will cache and restore the session
// across tests automatically.
Cypress.Commands.add('login', (token) => {
  cy.session(
    'admin-session',
    () => {
      cy.request({
        method: 'POST',
        url: '/login',
        form: true,
        body: { token },
        followRedirect: false,
      }).then((response) => {
        expect(response.status).to.eq(302);
      });
    },
    {
      validate() {
        // Verify session is still valid by checking we can reach a protected page
        cy.request({ url: '/servers' }).then((resp) => {
          // If we get redirected to login, session is invalid
          expect(resp.status).to.eq(200);
          expect(resp.body).to.include('Server Management');
        });
      },
    }
  );
});
