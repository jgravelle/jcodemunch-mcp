<%-- Source: https://github.com/microsoft/dotnet-framework-docker/blob/1c4b3a1f86a3429648bb6af07965d00e625910cf/samples/aspnetapp/aspnetapp/ViewSwitcher.ascx --%>
<%-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. --%>
<%-- Vendored verbatim except for this provenance header. --%>
<%@ Control Language="C#" AutoEventWireup="true" CodeBehind="ViewSwitcher.ascx.cs" Inherits="aspnetapp.ViewSwitcher" %>
<div id="viewSwitcher">
    <%: CurrentView %> view | <a href="<%: SwitchUrl %>" data-ajax="false">Switch to <%: AlternateView %></a>
</div>