<%-- HAND-WRITTEN fixture. No upstream source, deliberately. --%>
<%-- Every official Microsoft Web Forms sample that is MIT-licensed wires its --%>
<%-- events through AutoEventWireup + Page_Load and declares no On*= handlers, --%>
<%-- so the declarative-wiring case had no attributable source. The one perfect --%>
<%-- file (microsoft/Windows-classic-samples OpenSearch/Default.aspx) is under --%>
<%-- NOASSERTION and cannot be vendored. Written by hand instead, following the --%>
<%-- Counter.razor precedent for unattributed fixtures. --%>
<%@ Page Language="C#" AutoEventWireup="true" MasterPageFile="~/Site.Master" CodeBehind="webforms_handlers.aspx.cs" Inherits="Fixtures.HandlerWiring" %>

<asp:Content ID="Body" ContentPlaceHolderID="MainContent" runat="server">

    <%-- Capital OnClick: the common case. --%>
    <asp:Button ID="btnSave" runat="server" OnClick="btnSave_Click" Text="Save" />

    <%-- Lowercase onclick: emitted by older designers, and the reason a
         case-sensitive scan of this markup undercounts. --%>
    <asp:Button ID="btnFind" runat="server" onclick="btnFind_Click" Text="Find" />

    <%-- OnClientClick is a CLIENT-side JavaScript hook, not a codebehind edge.
         A scan that treats every On*= attribute as a handler reference counts
         `confirmDelete` as a server method and finds it missing. The server
         handler on the same control is OnClick, which must still resolve. --%>
    <asp:Button ID="btnDelete" runat="server"
        OnClientClick="return confirmDelete();"
        OnClick="btnDelete_Click" Text="Delete" />

    <%-- Non-Click events wire the same way. --%>
    <asp:DropDownList ID="ddlRegion" runat="server" AutoPostBack="true"
        OnSelectedIndexChanged="ddlRegion_SelectedIndexChanged" />

    <asp:GridView ID="gvOrders" runat="server"
        OnRowCommand="gvOrders_RowCommand"
        OnRowDataBound="gvOrders_RowDataBound" />

    <%-- Attribute names that CONTAIN "on" but are not events. A scan without a
         word boundary matches inside these and captures their VALUES as handler
         names -- ButtonType, HorizontalAlign and ControlToValidate each did. --%>
    <asp:CheckBox ID="cbAgree" runat="server" ButtonType="Button" />
    <asp:TableCell ID="tcHeader" runat="server" HorizontalAlign="Center" />
    <asp:RequiredFieldValidator ID="rfvName" runat="server"
        ControlToValidate="btnSave" ErrorMessage="Required" />

    <%-- Wired to a handler that does NOT exist in the codebehind. Real repos
         carry these from copy-paste between pages; a resolver must report it
         as unresolved rather than silently counting it as an edge. --%>
    <asp:Button ID="btnGhost" runat="server" OnClick="btnGhost_Click" Text="Ghost" />

</asp:Content>
