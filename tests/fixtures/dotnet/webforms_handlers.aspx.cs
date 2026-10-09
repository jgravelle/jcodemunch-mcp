// HAND-WRITTEN fixture, codebehind for webforms_handlers.aspx. No upstream source.
//
// Defines every handler that markup wires EXCEPT btnGhost_Click, which is absent
// on purpose so a resolver has an unresolved edge to report. Page_Load and
// Page_Init are bound by AutoEventWireup naming convention and are referenced by
// NOTHING in the markup -- that is the separate false-dead finding, and it cannot
// be fixed by parsing .aspx at all.

using System;
using System.Web.UI;
using System.Web.UI.WebControls;

namespace Fixtures
{
    public partial class HandlerWiring : Page
    {
        protected void Page_Init(object sender, EventArgs e)
        {
        }

        protected void Page_Load(object sender, EventArgs e)
        {
        }

        protected void btnSave_Click(object sender, EventArgs e)
        {
        }

        protected void btnFind_Click(object sender, EventArgs e)
        {
        }

        protected void btnDelete_Click(object sender, EventArgs e)
        {
        }

        protected void ddlRegion_SelectedIndexChanged(object sender, EventArgs e)
        {
        }

        protected void gvOrders_RowCommand(object sender, GridViewCommandEventArgs e)
        {
        }

        protected void gvOrders_RowDataBound(object sender, GridViewRowEventArgs e)
        {
        }

        // Not referenced from markup at all: a genuinely uncalled private method,
        // so a resolver that marks everything live is as wrong as one that marks
        // everything dead.
        private void UnusedHelper()
        {
        }
    }
}
